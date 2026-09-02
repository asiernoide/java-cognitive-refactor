"""
Bucle de refactorización con LLM (C3).

Para cada método del dataset con técnicas etiquetadas (1s), se intenta CADA
técnica por separado (enfoque propuesto por el cotutor):
  1. Extraer el método original de la copia de trabajo (out/).
  2. Pedir al LLM el código refactorizado con esa técnica.
  3. Aplicar el cambio en la copia, medir métricas y DESHACER el cambio (git).
  4. Quedarse con la mejor versión según el score penalizado
     S = ΔCC − λ·max(0, ΔCyclo) (ver más abajo); aplicar solo si S > 0.
La mejor versión se aplica de forma permanente y se commitea en la copia
(si su score es positivo). La copia original de projects/ queda intacta.

Config (.env):
    WORK_DIR                     copias de trabajo (default out)
    CC_THRESHOLD                 objetivo de CC (default 15)
    REFACTOR_CYCLO_PENALTY        lambda del score S(c)=ΔCC − λ·max(0, ΔCyclo)
                                 (default 1; 0 = seleccionar solo por CC)
    REFACTOR_MAX_CYCLO_DELTA_PCT red de seguridad opcional: % de aumento de
                                 ciclomática permitido (0 = sin límite)
    REFACTOR_SEED                semilla para el empate aleatorio (default 42)
    REFACTOR_RUN_TESTS           never/auto/always: cronometrar la suite de
                                 tests del proyecto antes/después del pase
    LLM_*                        cliente OpenAI-compatible (lib/llm_client.py)

La selección de la mejor versión usa un score con penalización:
    S(c) = ΔCC(c) − λ · max(0, ΔCyclo(c))
siendo ΔCC la reducción de complejidad cognitiva y ΔCyclo el aumento de
complejidad ciclomática respecto al método original. Se elige el candidato
de mayor S y solo se acepta si S > 0 (en caso contrario se mantiene el
original). Las fórmulas están documentadas en la nota Semana 10 (Obsidian).

Uso:
    python refactor_loop.py [--project X] [--limit N] [--dry-run]
"""

import argparse
import json
import os
import random
import re
import subprocess
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from lib import ast_analyzer as ast
from lib import llm_client, workspace

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
WORK_DIR = os.getenv("WORK_DIR", "out")
CC_THRESHOLD = int(os.getenv("CC_THRESHOLD", "15"))
CYCLO_PENALTY = float(os.getenv("REFACTOR_CYCLO_PENALTY", "1"))
MAX_CYCLO_DELTA_PCT = float(os.getenv("REFACTOR_MAX_CYCLO_DELTA_PCT", "0"))
SEED = int(os.getenv("REFACTOR_SEED", "42"))
RUN_TESTS = os.getenv("REFACTOR_RUN_TESTS", "never")

TARGETS = [
    "refactor_extract_method",
    "refactor_collapse_ifs_with_and",
    "refactor_lambda_map",
    "refactor_lambda_filter_map",
    "refactor_lambda_reduce",
]

TECHNIQUE_DESC = {
    "refactor_extract_method": (
        "Extract Method: extract cohesive blocks of the method body into new private "
        "method(s) and call them from the original method. Return the refactored original "
        "method and the new method(s)."
    ),
    "refactor_collapse_ifs_with_and": (
        "Collapse nested if statements into a single condition using && when it is "
        "semantically equivalent (the collapsed inner if must not have an else)."
    ),
    "refactor_lambda_map": (
        "Replace a foreach loop that transforms each element into a new collection with "
        "stream().map(...).collect(...)."
    ),
    "refactor_lambda_filter_map": (
        "Replace a foreach loop with a single if guard and a simple operation inside with "
        "stream().filter(...).map(...).collect(...)."
    ),
    "refactor_lambda_reduce": (
        "Replace a foreach loop that accumulates a single result (sum, count, "
        "concatenation, max/min) with stream().reduce(...) or stream().collect(...)."
    ),
}

SYSTEM_PROMPT = (
    "You are an expert Java refactoring assistant specialized in reducing cognitive "
    "complexity. Respond ONLY with the refactored method's Java source code: no markdown "
    "fences, no explanations, no commentary. Preserve the exact method signature and the "
    "behavior of the original code. Return a single method declaration."
)

SYSTEM_PROMPT_JSON = (
    "You are an expert Java refactoring assistant specialized in reducing cognitive "
    "complexity. Respond ONLY with a JSON object with exactly two fields: "
    "\"main_method\" (the refactored original method as a single Java method declaration, "
    "with the exact same signature) and \"new_methods\" (an array of new private Java "
    "method declarations). No other text, no markdown."
)

RNG = random.Random(SEED)
LOG_PATH = DATA_DIR / "refactor_log.jsonl"


# ---------------------------------------------------------------------------
# Utilidades de fichero / código
# ---------------------------------------------------------------------------

def read_lines(path: Path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines(keepends=True)


def write_lines(path: Path, lines):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(lines))


def file_eol(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4096)
    return "\r\n" if b"\r\n" in head else "\n"


def clean_code(content: str) -> str:
    code = content.strip()
    if code.startswith("```"):
        code = code.split("```", 2)[1] if code.count("```") >= 2 else code[3:]
    code = code.strip()
    # Quitar líneas sueltas de fence (p.ej. "java") que algunos modelos dejan
    lines = code.split("\n")
    while lines and (not lines[0].strip() or lines[0].strip().lower() == "java"):
        lines.pop(0)
    code = "\n".join(lines).strip()
    if not code.endswith("}"):
        code = code.rstrip() + "\n}"
    return code


def reindent(code: str, base_indent: str, eol: str):
    """Normaliza la indentación del bloque devuelto por el LLM usando como
    referencia relativa la indentación de la primera línea no vacía (la firma
    del método), y la re-posiciona a la indentación base del método original."""
    raw = code.strip("\n")
    lines = raw.split("\n")
    first = next((i for i, l in enumerate(lines) if l.strip()), 0)
    decl_indent = len(lines[first]) - len(lines[first].lstrip())
    out = []
    for l in lines:
        if l.strip():
            rel = max(0, (len(l) - len(l.lstrip())) - decl_indent)
            out.append(base_indent + " " * rel + l.strip())
        else:
            out.append("")
    return out, eol


def apply_candidate(full_path: Path, base: dict, main_code: str, new_methods: list[str]) -> bool:
    """Reemplaza el método original por main_code e inserta new_methods (si hay)
    justo después del método, dentro de la clase. Devuelve True si se aplicó."""
    start, end = base["method_start_line"], base["method_end_line"]
    lines = read_lines(full_path)
    if start < 1 or end > len(lines):
        return False
    eol = file_eol(full_path)
    base_indent = re.match(r"^[ \t]*", lines[start - 1]).group(0)
    block, eol = reindent(main_code, base_indent, eol)

    replacement = [l + eol for l in block]
    for nm in new_methods or []:
        nm_block, _ = reindent(nm, base_indent, eol)
        replacement.append(eol)  # línea en blanco separadora
        replacement.extend(l + eol for l in nm_block)

    lines[start - 1:end] = replacement
    write_lines(full_path, lines)
    return True


# ---------------------------------------------------------------------------
# LLM y candidatos
# ---------------------------------------------------------------------------

def extract_source(full_path: Path, base: dict) -> str:
    lines = read_lines(full_path)
    start, end = base["method_start_line"], base["method_end_line"]
    return "".join(lines[start - 1:end])


def try_technique(client, project: str, work_path: Path, rel_file: str, base: dict,
                  technique: str) -> dict | None:
    """Aplica una técnica: LLM -> aplicar -> medir -> deshacer. Devuelve el
    candidato con métricas, o None si el LLM no produjo algo válido."""
    full_path = work_path / rel_file
    source = extract_source(full_path, base)
    signature = base["method_signature"]

    if technique == "refactor_extract_method":
        system = SYSTEM_PROMPT_JSON
        user = (
            f"Refactor this method to reduce its cognitive complexity using the technique: "
            f"{TECHNIQUE_DESC[technique]}. Keep the exact signature `{signature}` for "
            "main_method. Do not change behavior.\n\n```java\n" + source + "\n```"
        )
        try:
            data, usage = client.chat_json([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
        except llm_client.LLMError as e:
            log_entry(project=project, file=rel_file, signature=signature,
                      technique=technique, reason=f"LLM error: {e}", valid=False)
            return None
        main_code = data.get("main_method", "")
        new_methods = data.get("new_methods") or []
    else:
        system = SYSTEM_PROMPT
        user = (
            f"Refactor this method to reduce its cognitive complexity using the technique: "
            f"{TECHNIQUE_DESC[technique]}. Keep the exact signature `{signature}`. "
            "Do not change behavior. Return only the Java code.\n\n```java\n" + source + "\n```"
        )
        try:
            content, usage = client.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
        except llm_client.LLMError as e:
            log_entry(project=project, file=rel_file, signature=signature,
                      technique=technique, reason=f"LLM error: {e}", valid=False)
            return None
        main_code = clean_code(content)
        new_methods = []

    if not apply_candidate(full_path, base, main_code, new_methods):
        return None

    # Medir tras el cambio (esto también valida que el archivo parsea)
    measured = ast.analyze_method_by_signature(str(full_path), signature)
    workspace.git_restore(work_path, rel_file)  # deshacer el cambio

    if measured is None:
        log_entry(project=project, file=rel_file, signature=signature,
                  technique=technique, reason="no parsea o no se encuentra el método",
                  valid=False)
        return None
    if measured.get("method_signature") != signature:
        log_entry(project=project, file=rel_file, signature=signature,
                  technique=technique, reason="la firma cambió", valid=False)
        return None

    tokens = (usage or {}).get("total_tokens", 0)
    return {
        "technique": technique,
        "cc": int(measured["cognitive_complexity"]),
        "cyclo": int(measured["cyclomatic_complexity"]),
        "loc": int(measured["loc"]),
        "invocations": int(measured["method_invocations"]),
        "tokens": tokens,
        "code": main_code,
        "new_methods": new_methods,
    }


def select_best(candidates: list[dict], base_cc: int, base_cyclo: int) -> tuple[dict | None, float]:
    """
    Selecciona el mejor candidato según el score con penalización:

        S(c) = ΔCC(c) − λ · max(0, ΔCyclo(c))

    donde ΔCC(c) = base_cc − c.cc (reducción de complejidad cognitiva) y
    ΔCyclo(c) = c.cyclo − base_cyclo (aumento de complejidad ciclomática).

    Solo se penaliza que la ciclomática suba (max(0, …)); si baja o se
    mantiene no hay castigo. Devuelve (mejor_candidato, score); si el score
    del mejor es <= 0, no merece la pena y debe mantenerse el original.
    Empate de score -> menor ciclomática -> aleatorio (semilla fija).
    """
    best = None
    best_key = None
    for c in candidates:
        dcc = base_cc - c["cc"]
        dcyclo = c["cyclo"] - base_cyclo
        score = dcc - CYCLO_PENALTY * max(0, dcyclo)
        c["score"] = round(score, 3)
        key = (-score, c["cyclo"], RNG.random())
        if best_key is None or key < best_key:
            best_key = key
            best = c
    return best, (best or {}).get("score", 0.0)


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

def log_entry(**fields):
    fields.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Tests (cronometraje empírico por proyecto)
# ---------------------------------------------------------------------------

def detect_build_tool(project_path: Path) -> str | None:
    if (project_path / "pom.xml").exists():
        return "mvn"
    if (project_path / "build.gradle").exists() or (project_path / "build.gradle.kts").exists():
        return "gradle"
    if (project_path / "build.xml").exists():
        return "ant"
    return None


def run_tests_timed(project_path: Path) -> dict | None:
    tool = detect_build_tool(project_path)
    if tool is None:
        return None
    cmds = {"mvn": ["mvn", "-q", "test"], "gradle": ["gradle", "test"], "ant": ["ant", "test"]}
    t0 = time.time()
    try:
        r = subprocess.run(cmds[tool], cwd=str(project_path), capture_output=True,
                           text=True, timeout=1800)
        return {
            "tool": tool,
            "wall_seconds": round(time.time() - t0, 1),
            "success": r.returncode == 0,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"tool": tool, "wall_seconds": None, "success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Bucle principal
# ---------------------------------------------------------------------------

def process_method(project: str, work_path: Path, row, client, dry_run: bool, stats: dict):
    rel_file = str(row["file"])
    full_path = work_path / rel_file
    if not full_path.is_file():
        stats["skipped_no_file"] += 1
        return

    techniques = [t for t in TARGETS if int(row.get(t) or 0) == 1]
    if not techniques:
        stats["skipped_no_technique"] += 1
        return

    base = ast.analyze_method(str(full_path), int(row["method_start_line"]))
    if base is None:
        stats["skipped_not_found"] += 1
        log_entry(project=project, file=rel_file, technique="any",
                  reason="método no localizado en la copia", valid=False)
        return

    signature = base["method_signature"]
    base_cc = int(base["cognitive_complexity"])
    base_cyclo = int(base["cyclomatic_complexity"])

    candidates = []
    for t in techniques:
        cand = try_technique(client, project, work_path, rel_file, base, t)
        if cand is not None:
            candidates.append(cand)
        stats["candidates"] += 1

    if not candidates:
        stats["no_valid_candidate"] += 1
        log_entry(project=project, file=rel_file, signature=signature,
                  base_cc=base_cc, reason="ninguna técnica generó un candidato válido")
        return

    # Red de seguridad opcional: cap porcentual de ciclomática (0 = sin límite)
    if MAX_CYCLO_DELTA_PCT > 0:
        ref_cyclo = base_cyclo if base_cyclo > 0 else 1
        allowed = [c for c in candidates
                   if (c["cyclo"] - ref_cyclo) / ref_cyclo * 100 <= MAX_CYCLO_DELTA_PCT]
        if allowed:
            candidates = allowed
            log_entry(project=project, file=rel_file, signature=signature,
                      base_cc=base_cc, base_cyclo=base_cyclo,
                      reason=f"descartados candidatos con Δciclomática > {MAX_CYCLO_DELTA_PCT}%")
        else:
            stats["kept_original"] += 1
            log_entry(project=project, file=rel_file, signature=signature,
                      base_cc=base_cc, base_cyclo=base_cyclo,
                      decision="keep_original",
                      reason="todos los candidatos superan el límite de ciclomática (safety)")
            return

    # Selección por score penalizado: S = ΔCC − λ·max(0, ΔCyclo)
    best, score = select_best(candidates, base_cc, base_cyclo)

    for c in candidates:
        log_entry(project=project, file=rel_file, signature=signature,
                  technique=c["technique"], base_cc=base_cc, base_cyclo=base_cyclo,
                  candidate_cc=c["cc"], candidate_cyclo=c["cyclo"],
                  candidate_loc=c["loc"], candidate_invocations=c["invocations"],
                  score=c.get("score"), tokens=c["tokens"],
                  valid=True, kept=(c is best))

    if score <= 0:
        stats["kept_original"] += 1
        log_entry(project=project, file=rel_file, signature=signature,
                  base_cc=base_cc, base_cyclo=base_cyclo,
                  decision="keep_original",
                  reason=f"ninguna versión merece la pena (mejor score {score:.2f} <= 0)")
        return

    # Aplicar la mejor versión de forma permanente
    if not apply_candidate(full_path, base, best["code"], best["new_methods"]):
        stats["apply_failed"] += 1
        return

    if dry_run:
        workspace.git_restore(work_path, rel_file)
        decision = "kept(dry-run)"
        stats["kept_dry_run"] += 1
    else:
        workspace.git_commit(
            work_path,
            f"refactor: {best['technique'].replace('refactor_', '')} CC {base_cc}->{best['cc']} "
            f"{rel_file} {signature}",
        )
        decision = "kept+commit"
        stats["kept_committed"] += 1

    log_entry(project=project, file=rel_file, signature=signature,
              base_cc=base_cc, base_cyclo=base_cyclo,
              technique=best["technique"], candidate_cc=best["cc"],
              candidate_cyclo=best["cyclo"], candidate_loc=best["loc"],
              candidate_invocations=best["invocations"],
              decision=decision)
    stats["methods_refactored"] += 1


def main():
    parser = argparse.ArgumentParser(description="Bucle de refactorización con LLM")
    parser.add_argument("--project", help="Proyecto concreto (carpeta en projects/)")
    parser.add_argument("--limit", type=int, help="Máximo de métodos a procesar")
    parser.add_argument("--dry-run", action="store_true",
                        help="No deja cambios permanentes ni commits en las copias")
    args = parser.parse_args()

    client = llm_client.LLMClient()
    if not client.api_key:
        print("ERROR: LLM_API_KEY no definida en .env")
        return

    print("Config:")
    print(f"  WORK_DIR={WORK_DIR} CC_THRESHOLD={CC_THRESHOLD} "
          f"CYCLO_PENALTY={CYCLO_PENALTY} MAX_CYCLO_DELTA_PCT={MAX_CYCLO_DELTA_PCT} "
          f"SEED={SEED} RUN_TESTS={RUN_TESTS}")
    print(f"  LLM: {client.base_url}  model={client.model}  "
          f"reasoning_effort={client.reasoning_effort}")

    pairs = workspace.ensure_workspace(PROJECTS_DIR, WORK_DIR)
    work_map = {name: path for name, path in pairs}

    stats = {k: 0 for k in [
        "skipped_no_file", "skipped_no_technique", "skipped_not_found",
        "candidates", "no_valid_candidate", "kept_original", "kept_committed",
        "kept_dry_run", "apply_failed", "methods_refactored",
    ]}

    csvs = sorted(DATA_DIR.glob("final_methods_dataset_*.csv"))
    if args.project:
        csvs = [c for c in csvs if c.name == f"final_methods_dataset_{args.project}.csv"]

    total_processed = 0
    for csv_path in csvs:
        project = csv_path.name.removeprefix("final_methods_dataset_").removesuffix(".csv")
        if project not in work_map:
            continue
        work_path = work_map[project]

        test_baseline = None
        if RUN_TESTS in ("auto", "always") and not args.dry_run:
            tool = detect_build_tool(work_path)
            has_tests = (work_path / "src" / "test").exists() or (work_path / "test").exists()
            if tool and (RUN_TESTS == "always" or has_tests):
                print(f"[tests] baseline {project} ...")
                test_baseline = run_tests_timed(work_path)

        df = pd.read_csv(csv_path)
        print(f"\n=== {project} ({len(df)} métodos) ===")
        for _, row in df.iterrows():
            if args.limit and total_processed >= args.limit:
                break
            process_method(project, work_path, row, client, args.dry_run, stats)
            total_processed += 1
            if total_processed % 10 == 0:
                print(f"  ... {total_processed} métodos procesados")

        if test_baseline is not None:
            after = run_tests_timed(work_path)
            log_entry(event="tests", project=project,
                      baseline_seconds=test_baseline.get("wall_seconds"),
                      baseline_success=test_baseline.get("success"),
                      after_seconds=after.get("wall_seconds") if after else None,
                      after_success=after.get("success") if after else None)
            print(f"[tests] {project}: baseline={test_baseline} after={after}")

        if args.limit and total_processed >= args.limit:
            break

    print("\n--- Resumen ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"Log: {LOG_PATH}")


if __name__ == "__main__":
    main()