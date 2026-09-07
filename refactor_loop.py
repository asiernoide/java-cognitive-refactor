"""
Bucle de refactorización con LLM.

Para cada método del dataset con técnicas etiquetadas (1s), se generan
candidatos refactorizados y se elige el mejor según el score penalizado
S = ΔCC − λ·max(0, ΔCyclo); solo se aplica y commitea si S > 0. La copia
original de projects/ queda intacta; el trabajo se hace en copias (out/).

Dos modos de llamada al LLM (REFACTOR_MODE):
  - stream: una llamada por técnica y método.
  - batch: agrupa varios métodos en UNA llamada (JSON). Los métodos que fallan
    (no parsean, JSON inválido, etc.) se REINTENTAN en rondas dirigidas
    (REFACTOR_BATCH_RETRIES) enviando el error exacto como feedback al LLM;
    si el lote entero falla, se parte por la mitad. Usa streaming + guardia de
    reloj adaptativa (el gateway corta las respuestas no-streaming largas).

En Extract Method, la CC/Ciclomática del candidato es el TOTAL
(método principal + suma de los métodos extraídos) para no reducir la CC
artificialmente escondiendo lógica en sub-métodos.

Config (.env):
    WORK_DIR                     copias de trabajo (default out)
    CC_THRESHOLD                 objetivo de CC (default 15)
    REFACTOR_CYCLO_PENALTY        lambda del score S(c)=ΔCC − λ·max(0, ΔCyclo)
                                 (default 1; 0 = seleccionar solo por CC)
    REFACTOR_MAX_CYCLO_DELTA_PCT red de seguridad opcional: % de aumento de
                                 ciclomática permitido (0 = sin límite)
    REFACTOR_SEED                semilla para el empate aleatorio (default 42)
    REFACTOR_MAX_TOKENS          máx. tokens de salida por llamada en stream
                                 (default 24000)
    REFACTOR_MODE                stream | batch (default stream)
    REFACTOR_BATCH_MAX_TOKENS    presupuesto de SALIDA por llamada batch
                                 (default 100000; DeepSeek permite ~384k)
    REFACTOR_BATCH_TIMEOUT       guardia de reloj mínima por llamada batch,
                                 adaptativa al tamaño del lote (default 600s)
    REFACTOR_BATCH_RETRIES       rondas de reintento dirigido de fallidos
                                 (default 2)
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
                           [--fresh] [--log FICHERO] [--resume FICHERO] [--quiet]
    python run_parallel.py -n 4 [--fresh] [--resume FICHERO]   # paralelo
"""

import argparse
import json
import os
import random
import re
import subprocess
import tempfile
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
REFACTOR_MAX_TOKENS = int(os.getenv("REFACTOR_MAX_TOKENS", "24000"))
RUN_TESTS = os.getenv("REFACTOR_RUN_TESTS", "never")
# Modo de trabajo: "stream" = una llamada LLM por método (por defecto);
# "batch" = agrupa varios métodos en UNA llamada (más barato en latencia).
REFACTOR_MODE = os.getenv("REFACTOR_MODE", "stream")
# Presupuesto de tokens de SALIDA por llamada en modo batch. DeepSeek V4 Flash
# permite hasta ~384k de salida; con lotes grandes el coste por llamada es una
# sola vez y los fallos se recuperan con reintentos dirigidos (ver abajo).
REFACTOR_BATCH_MAX_TOKENS = int(os.getenv("REFACTOR_BATCH_MAX_TOKENS", "100000"))
# Timeout (guardia de reloj) por llamada en modo batch. Se ajusta en proceso
# según el tamaño del lote (véase process_batch): guardia = max(este valor,
# tiempo estimado de generación × 2).
REFACTOR_BATCH_TIMEOUT = int(os.getenv("REFACTOR_BATCH_TIMEOUT", "600"))
# Reintentos dirigidos: rondas extra que reenvían SOLO los métodos fallidos del
# lote, indicando al LLM el error exacto para que lo corrija.
REFACTOR_BATCH_RETRIES = int(os.getenv("REFACTOR_BATCH_RETRIES", "2"))

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

SYSTEM_PROMPT_BATCH = (
    "You are an expert Java refactoring assistant specialized in reducing cognitive "
    "complexity. You receive a JSON object with an array of Java methods to refactor. "
    "Respond ONLY with a JSON object with a single field \"refactors\": an array of "
    "entries. For EVERY method id, provide ONE entry for EACH technique in its "
    "\"techniques\" array (e.g. if a method lists 2 techniques, output 2 entries for it). "
    "Each entry must be a JSON object with exactly: \"id\" (the input method id, an "
    "integer), \"technique\" (one of the allowed techniques for that method), "
    "\"main_method\" (the refactored original method as a single Java method declaration "
    "with the exact same signature as the input), and \"new_methods\" (an array of new "
    "private Java method declarations; an empty array when the technique is not Extract "
    "Method). Preserve the behavior of every method exactly. No text outside the JSON."
)

SYSTEM_PROMPT_BATCH_RETRY = (
    "You are an expert Java refactoring assistant specialized in reducing cognitive "
    "complexity. You receive a JSON object with an array of Java methods that FAILED in "
    "a previous refactoring attempt. Each method includes an \"error\" field with the "
    "exact error (typically a Java parser/compile error) that the previous attempt "
    "produced. Respond ONLY with a JSON object with a single field \"refactors\": an "
    "array of entries, one per input method id (every method must be retried). Each "
    "entry must be a JSON object with exactly: \"id\" (the input method id, an integer), "
    "\"technique\" (one of the allowed techniques for that method), \"main_method\" (the "
    "refactored original method as a single Java method declaration with the exact same "
    "signature as the input), and \"new_methods\" (an array of new private Java method "
    "declarations; an empty array when the technique is not Extract Method). FIX the "
    "error shown in each method's \"error\" field: the result MUST be syntactically "
    "valid Java that parses. Preserve behavior exactly. No text outside the JSON."
)

RNG = random.Random(SEED)
LOG_PATH = DATA_DIR / "refactor_log.jsonl"


# ---------------------------------------------------------------------------
# Progreso en vivo y reanudación
# ---------------------------------------------------------------------------

def fmt_eta(seconds: float) -> str:
    s = int(seconds)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


class Progress:
    """Progreso en vivo del bucle: una línea con \\r en terminal y una línea de
    control (checkpoint) cada N métodos para poder seguir el avance desde el
    fichero de salida cuando se ejecuta en segundo plano."""

    def __init__(self, total: int, labeled_total: int, checkpoint_every: int = 25,
                 quiet: bool = False):
        self.total = total
        self.labeled_total = labeled_total
        self.checkpoint_every = checkpoint_every
        self.quiet = quiet
        self.processed = 0
        self.labeled_done = 0
        self.resume_skipped = 0
        self.counts = {}
        self.t0 = time.time()
        self._last = 0.0

    def update(self, status: str, labeled: bool = False, resumed: bool = False):
        self.processed += 1
        if resumed:
            # Los reanudados se muestran una sola vez (via resume_skipped),
            # no duplicados en counts con la clave "reanudado".
            self.resume_skipped += 1
        else:
            self.counts[status] = self.counts.get(status, 0) + 1
            if labeled:
                self.labeled_done += 1
        self._render_live()
        if self.processed % self.checkpoint_every == 0:
            self._render_checkpoint()

    def _counts_str(self) -> str:
        parts = ["%s=%d" % (k, v) for k, v in sorted(self.counts.items())]
        if self.resume_skipped:
            parts.append("reanudados=%d" % self.resume_skipped)
        return " ".join(parts)

    def _rate(self):
        elapsed = time.time() - self.t0
        return self.processed / elapsed if elapsed > 0 else 0.0, elapsed

    def _eta(self):
        """ETA basado SOLO en el ritmo de los métodos etiquetados (los que hacen
        trabajo LLM). El ritmo global mezcla los métodos instantáneos
        (no_technique, not_found, reanudados) y deja el ETA demasiado optimista
        (p. ej. '12 min' cuando faltan ~1h). Los métodos rápidos restantes se
        estiman a ~60/s."""
        elapsed = time.time() - self.t0
        if self.labeled_done <= 0 or elapsed <= 0:
            return "?"
        labeled_rate = self.labeled_done / elapsed
        remaining_labeled = max(0, self.labeled_total - self.labeled_done)
        remaining_total = max(0, self.total - self.processed)
        remaining_fast = max(0, remaining_total - remaining_labeled)
        eta_labeled = remaining_labeled / labeled_rate if labeled_rate > 0 else 0.0
        eta_fast = remaining_fast / 60.0 if remaining_fast > 0 else 0.0
        return fmt_eta(eta_labeled + eta_fast)

    def _render_live(self):
        if self.quiet:
            return
        now = time.time()
        if now - self._last < 2:
            return
        self._last = now
        rate, elapsed = self._rate()
        print("\r[%s] %d/%d métodos (%d con técnica) | ETA %s | %s     " % (
            fmt_eta(elapsed), self.processed, self.total, self.labeled_done,
            self._eta(), self._counts_str()), end="", flush=True)

    def _render_checkpoint(self):
        _, elapsed = self._rate()
        print("\n[CHECKPOINT] %d/%d métodos (%d con técnica) | transcurrido %s | ETA %s | %s"
              % (self.processed, self.total, self.labeled_done, fmt_eta(elapsed),
                 self._eta(), self._counts_str()), flush=True)

    def finish(self):
        print("\n--- Fin del bucle ---")
        print(self._counts_str(), flush=True)


def load_done_keys(resume_path: Path | None = None) -> set[tuple[str, str, int]]:
    """Conjunto de métodos ya procesados (project, file, start_line) a partir de
    las entradas del log con campo `status` (marcan el final del procesamiento
    de un método). Permite reanudar sin repetir trabajo ni gastar tokens.
    `resume_path` permite leer un log distinto del de salida (paralelismo)."""
    path = resume_path or LOG_PATH
    keys = set()
    if not path.exists():
        return keys
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("status") and e.get("status") != "llm_error" \
                    and e.get("start_line") is not None:
                keys.add((e["project"], e["file"], int(e["start_line"])))
    return keys


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


def measure_method_snippet(source: str) -> dict | None:
    """Mide CC/ciclomática/LOC/invocations de un método aislado (p.ej. un método
    extraído por Extract Method) envolviéndolo en una clase temporal. Devuelve
    las métricas o None si no parsea."""
    code = "class T {\n" + source + "\n}\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".java", delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        return ast.analyze_method(tmp, 2)
    finally:
        if tmp:
            os.unlink(tmp)


def try_technique(client, project: str, work_path: Path, rel_file: str, base: dict,
                  technique: str, locate_sig: str) -> dict | None:
    """Aplica una técnica: LLM -> aplicar -> medir -> deshacer. Devuelve el
    candidato con métricas, o None si el LLM no produjo algo válido.

    `locate_sig` es la firma cualificada (enclosing_class.method_signature),
    única dentro del fichero y estable ante refactors; se usa para re-localizar
    el método tras aplicar el cambio (la firma simple puede ser ambigua en
    clases internas y las líneas cambian tras el refactor)."""
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
            ], max_tokens=REFACTOR_MAX_TOKENS)
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
            ], max_tokens=REFACTOR_MAX_TOKENS)
        except llm_client.LLMError as e:
            log_entry(project=project, file=rel_file, signature=signature,
                      technique=technique, reason=f"LLM error: {e}", valid=False)
            return None
        main_code = clean_code(content)
        new_methods = []

    # Guard: rechazar salidas vacías o que no parecen un método Java
    if not main_code.strip() or len(main_code.strip()) < 20 or "(" not in main_code or "{" not in main_code:
        log_entry(project=project, file=rel_file, signature=signature,
                  technique=technique, reason="salida del LLM vacía o demasiado corta",
                  valid=False)
        return None

    tokens = (usage or {}).get("total_tokens", 0)
    return evaluate_candidate(work_path, rel_file, base, locate_sig, technique,
                              main_code, new_methods, project, tokens=tokens)


def evaluate_candidate(work_path: Path, rel_file: str, base: dict, locate_sig: str,
                       technique: str, main_code: str, new_methods: list,
                       project: str, tokens: int = 0) -> dict | None:
    """Aplica el candidato a la copia, mide, revierte y devuelve el dict del
    candidato. Comparte la medición con el modo stream y el modo batch.
    None si no valida (no parsea o cambia la firma).

    CC/Ciclomática EFECTIVA: para Extract Method se suma la complejidad de los
    métodos extraídos (si solo se midiera el método principal, la extracción
    permitiría reducir la CC artificialmente "escondiendo" la lógica en
    sub-métodos). Sin esa suma, el total podría incluso aumentar.
    """
    full_path = work_path / rel_file
    signature = base["method_signature"]

    if not apply_candidate(full_path, base, main_code, new_methods):
        return None

    # Medir tras el cambio (esto también valida que el archivo parsea).
    # Se re-localiza por la firma CUALIFICADA (estable ante cambios de línea y
    # sin ambigüedad de clases internas), no por la línea original.
    measured = ast.analyze_method_by_signature(str(full_path), locate_sig)
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

    main_cc = int(measured["cognitive_complexity"])
    main_cyclo = int(measured["cyclomatic_complexity"])
    extracted_cc = 0
    extracted_cyclo = 0
    extracted_loc = 0
    extracted_inv = 0
    if technique == "refactor_extract_method":
        for nm in new_methods:
            m = measure_method_snippet(nm)
            if m is not None:
                extracted_cc += int(m["cognitive_complexity"])
                extracted_cyclo += int(m["cyclomatic_complexity"])
                extracted_loc += int(m["loc"])
                extracted_inv += int(m["method_invocations"])

    return {
        "technique": technique,
        "cc": main_cc + extracted_cc,
        "cyclo": main_cyclo + extracted_cyclo,
        "loc": int(measured["loc"]) + extracted_loc,
        "invocations": int(measured["method_invocations"]) + extracted_inv,
        "tokens": tokens,
        "code": main_code,
        "new_methods": new_methods,
        "main_cc": main_cc,
        "extracted_cc": extracted_cc,
        "main_cyclo": main_cyclo,
        "extracted_cyclo": extracted_cyclo,
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

def process_method(project: str, work_path: Path, row, client, dry_run: bool, stats: dict) -> str:
    """Procesa un método y devuelve su estado (checkpoint de reanudación)."""
    rel_file = str(row["file"])
    start_line = int(row["method_start_line"])
    full_path = work_path / rel_file
    if not full_path.is_file():
        stats["skipped_no_file"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  status="no_file", reason="archivo no existe en la copia")
        return "no_file"

    techniques = [t for t in TARGETS if int(row.get(t) or 0) == 1]
    if not techniques:
        stats["skipped_no_technique"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  status="no_technique")
        return "no_technique"

    # Identidad ESTABLE del método desde el proyecto ORIGINAL (nunca modificado):
    # la línea del CSV del dataset solo es válida en el original; la copia de
    # trabajo puede haber cambiado de líneas tras refactors previos en el mismo
    # archivo. La firma cualificada (enclosing_class.method_signature) es única
    # dentro del fichero (desambigua clases internas, p.ej. JSONPath.SizeSegment)
    # y no cambia con los refactors.
    orig_path = Path(PROJECTS_DIR) / project / rel_file
    orig_meta = ast.analyze_method(str(orig_path), start_line)
    if orig_meta is None:
        stats["skipped_not_found"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  technique="any", reason="método no localizado en el proyecto original",
                  status="not_found", valid=False)
        return "not_found"

    enclosing = orig_meta.get("enclosing_class") or ""
    locate_sig = f"{enclosing}.{orig_meta['method_signature']}" if enclosing \
        else orig_meta["method_signature"]

    # Localizar en la COPIA por firma cualificada (estable ante cambios de línea)
    base = ast.analyze_method_by_signature(str(full_path), locate_sig)
    if base is None:
        stats["skipped_not_found"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  signature=orig_meta["method_signature"],
                  reason="método no localizado en la copia (firma cualificada)",
                  status="not_found", valid=False)
        return "not_found"

    signature = base["method_signature"]
    base_cc = int(base["cognitive_complexity"])
    base_cyclo = int(base["cyclomatic_complexity"])

    candidates = []
    for t in techniques:
        cand = try_technique(client, project, work_path, rel_file, base, t, locate_sig)
        if cand is not None:
            candidates.append(cand)
        stats["candidates"] += 1

    return finalize_method(project, work_path, rel_file, start_line, signature,
                           locate_sig, base_cc, base_cyclo, candidates, dry_run, stats)


def finalize_method(project: str, work_path: Path, rel_file: str, start_line: int,
                    signature: str, locate_sig: str, base_cc: int, base_cyclo: int,
                    candidates: list[dict], dry_run: bool, stats: dict) -> str:
    """Decide y aplica el mejor candidato con nuestros criterios (mismo flujo para
    modo stream y batch): selección por score, aplicación permanente, commit, log."""
    if not candidates:
        stats["no_valid_candidate"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  signature=signature, base_cc=base_cc,
                  reason="ninguna técnica generó un candidato válido",
                  status="no_valid_candidate")
        return "no_valid_candidate"

    # Red de seguridad opcional: cap porcentual de ciclomática (0 = sin límite)
    if MAX_CYCLO_DELTA_PCT > 0:
        ref_cyclo = base_cyclo if base_cyclo > 0 else 1
        allowed = [c for c in candidates
                   if (c["cyclo"] - ref_cyclo) / ref_cyclo * 100 <= MAX_CYCLO_DELTA_PCT]
        if allowed:
            candidates = allowed
            log_entry(project=project, file=rel_file, start_line=start_line,
                      signature=signature, base_cc=base_cc, base_cyclo=base_cyclo,
                      reason=f"descartados candidatos con Δciclomática > {MAX_CYCLO_DELTA_PCT}%")
        else:
            stats["kept_original"] += 1
            log_entry(project=project, file=rel_file, start_line=start_line,
                      signature=signature, base_cc=base_cc, base_cyclo=base_cyclo,
                      status="keep_original", decision="keep_original",
                      reason="todos los candidatos superan el límite de ciclomática (safety)")
            return "keep_original"

    # Selección por score penalizado: S = ΔCC − λ·max(0, ΔCyclo)
    best, score = select_best(candidates, base_cc, base_cyclo)

    for c in candidates:
        log_entry(project=project, file=rel_file, start_line=start_line,
                  signature=signature, technique=c["technique"],
                  base_cc=base_cc, base_cyclo=base_cyclo,
                  candidate_cc=c["cc"], candidate_cyclo=c["cyclo"],
                  candidate_loc=c["loc"], candidate_invocations=c["invocations"],
                  main_cc=c.get("main_cc"), extracted_cc=c.get("extracted_cc"),
                  main_cyclo=c.get("main_cyclo"), extracted_cyclo=c.get("extracted_cyclo"),
                  score=c.get("score"), tokens=c["tokens"],
                  valid=True, kept=(c is best))

    if score <= 0:
        stats["kept_original"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  signature=signature, base_cc=base_cc, base_cyclo=base_cyclo,
                  status="keep_original", decision="keep_original",
                  reason=f"ninguna versión merece la pena (mejor score {score:.2f} <= 0)")
        return "keep_original"

    # Re-localizar el método en la copia justo antes de aplicar: en modo batch
    # varios métodos del mismo fichero pueden haberse aplicado antes y desplazar
    # las líneas; la firma cualificada es estable ante esos cambios.
    full_path = work_path / rel_file
    current = ast.analyze_method_by_signature(str(full_path), locate_sig)
    if current is None or not apply_candidate(full_path, current, best["code"], best["new_methods"]):
        stats["apply_failed"] += 1
        log_entry(project=project, file=rel_file, start_line=start_line,
                  signature=signature, status="apply_failed",
                  reason="no se pudo aplicar el cambio")
        return "apply_failed"

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

    log_entry(project=project, file=rel_file, start_line=start_line,
              signature=signature, base_cc=base_cc, base_cyclo=base_cyclo,
              status="kept", decision=decision,
              technique=best["technique"], candidate_cc=best["cc"],
              candidate_cyclo=best["cyclo"], candidate_loc=best["loc"],
              candidate_invocations=best["invocations"],
              score=best.get("score"))
    stats["methods_refactored"] += 1
    return "kept"


def batch_entry(project: str, work_path: Path, row) -> tuple[dict | None, str | None]:
    """Prepara un método para el modo batch. Devuelve (entry, error) donde error
    es el estado si el método no puede entrar en el batch (no_file, no_technique,
    not_found) o None si entra. La source se toma del proyecto ORIGINAL (baseline):
    los métodos de un batch no se han refactorizado aún, por lo que la copia de
    trabajo está idéntica al original."""
    rel_file = str(row["file"])
    start_line = int(row["method_start_line"])
    full_path = work_path / rel_file
    if not full_path.is_file():
        return None, "no_file"
    techniques = [t for t in TARGETS if int(row.get(t) or 0) == 1]
    if not techniques:
        return None, "no_technique"

    orig_path = Path(PROJECTS_DIR) / project / rel_file
    orig_meta = ast.analyze_method(str(orig_path), start_line)
    if orig_meta is None:
        return None, "not_found"
    enclosing = orig_meta.get("enclosing_class") or ""
    locate_sig = f"{enclosing}.{orig_meta['method_signature']}" if enclosing \
        else orig_meta["method_signature"]

    base = ast.analyze_method_by_signature(str(full_path), locate_sig)
    if base is None:
        return None, "not_found"

    return {
        "project": project,
        "rel_file": rel_file,
        "start_line": start_line,
        "signature": base["method_signature"],
        "locate_sig": locate_sig,
        "techniques": techniques,
        "source": extract_source(orig_path, orig_meta),
        "base": base,
        "base_cc": int(base["cognitive_complexity"]),
        "base_cyclo": int(base["cyclomatic_complexity"]),
    }, None


def _batch_tokens(entries: list[dict]) -> int:
    """Estimación de los tokens de SALIDA del batch (lo que debe caber en
    max_tokens): por cada método y técnica, una refactorización de tamaño ~ el
    del método. Se usa el 80% del tope como margen: si un batch se pasa del
    max_tokens el JSON llega truncado ('JSON sin cerrar') y se pierde el lote."""
    return int(sum(len(e["source"]) // 3 * max(1, len(e["techniques"])) + 200
                   for e in entries))


def _batch_call(client, entries: list[dict], stats: dict,
                errors: dict | None = None) -> tuple[dict | None, dict | None]:
    """Una llamada batch (streaming + guardia de reloj adaptativa). Devuelve
    (data, usage) o (None, None) si fallan los 3 intentos. Con `errors`
    (dict id -> mensaje de error) reenvía solo los métodos fallidos indicando al
    LLM el error exacto para que lo corrija (prompt de reintento)."""
    project = entries[0]["project"]
    methods = []
    for i, e in enumerate(entries):
        m = {"id": i, "signature": e["signature"], "techniques": e["techniques"],
             "source": e["source"]}
        if errors:
            m["error"] = errors.get(i, "")
        methods.append(m)
    user = json.dumps({"methods": methods}, ensure_ascii=False)
    system = SYSTEM_PROMPT_BATCH_RETRY if errors else SYSTEM_PROMPT_BATCH
    # Guardia de reloj adaptativa: la generación va a ~75 tok/s; doble margen.
    est = _batch_tokens(entries)
    guard = max(REFACTOR_BATCH_TIMEOUT, int(est / 75) * 2 + 120)
    for attempt in range(3):
        try:
            data, usage = client.chat_json([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ], max_tokens=REFACTOR_BATCH_MAX_TOKENS, timeout=guard, stream=True)
            return data, usage
        except llm_client.LLMError as e:
            stats["llm_errors"] += 1
            log_entry(event="batch", project=project, methods=len(entries),
                      reason=f"LLM error: {e}")
            time.sleep(3 ** attempt)
    return None, None


def _group_refactors(data: dict | None) -> dict[int, list[dict]]:
    by: dict[int, list[dict]] = {}
    for r in (data or {}).get("refactors") or []:
        if isinstance(r, dict) and isinstance(r.get("id"), int):
            by.setdefault(r["id"], []).append(r)
    return by


def _evaluate_batch_method(work_path: Path, entry: dict, refactors: list[dict],
                           batch_tokens: int, stats: dict) -> tuple[list[dict], str]:
    """Evalúa los refactors devueltos para un método (aplicar→medir→revertir).
    Devuelve (candidatos, error_concat) donde error_concat está vacío si hay
    candidatos o contiene el/los motivos del fallo (para el feedback dirigido)."""
    candidates = []
    errors = []
    for r in refactors:
        technique = r.get("technique")
        main_code = r.get("main_method") or ""
        new_methods = r.get("new_methods") or []
        if technique not in entry["techniques"]:
            errors.append(f"técnica '{technique}' no permitida")
            continue
        if not main_code.strip() or len(main_code.strip()) < 20 \
                or "(" not in main_code or "{" not in main_code:
            errors.append("salida vacía o demasiado corta")
            continue
        cand = evaluate_candidate(work_path, entry["rel_file"], entry["base"],
                                  entry["locate_sig"], technique, main_code,
                                  new_methods, entry["project"], tokens=batch_tokens)
        if cand is not None:
            candidates.append(cand)
        else:
            errors.append(ast.last_error or "no parsea o cambia la firma")
        stats["candidates"] += 1
    if not refactors:
        errors.append("el LLM no devolvió ningún refactor para este método")
    return candidates, " | ".join(dict.fromkeys(errors))


def _retry_batch(client, work_path: Path, failed: list[tuple[dict, str]],
                 batch_tokens: int, stats: dict, retries_left: int):
    """Rondas de reintento dirigido sobre la lista (entry, error). Devuelve
    (results_fixed, still_failed). Cada ronda reenvía SOLO los métodos fallidos
    con el error exacto como feedback."""
    results = []
    while failed and retries_left > 0:
        retries_left -= 1
        stats["batch_retries"] += 1
        entries = [e for e, _ in failed]
        errors = {i: err for i, (_, err) in enumerate(failed)}
        data, _usage = _batch_call(client, entries, stats, errors=errors)
        if data is None:
            # El reintento también falló del todo: partir y reintentar cada mitad
            if len(entries) > 1:
                mid = len(entries) // 2
                r1, f1 = _retry_batch(client, work_path, failed[:mid], batch_tokens,
                                      stats, retries_left)
                r2, f2 = _retry_batch(client, work_path, failed[mid:], batch_tokens,
                                      stats, retries_left)
                return results + r1 + r2, f1 + f2
            break
        by_id = _group_refactors(data)
        new_failed = []
        for i, (e, _old) in enumerate(failed):
            cands, err = _evaluate_batch_method(work_path, e, by_id.get(i, []),
                                                batch_tokens, stats)
            if cands:
                results.append((e, cands))
            else:
                new_failed.append((e, err or "el LLM no devolvió ningún refactor válido"))
        failed = new_failed
    return results, failed


def process_batch(client, work_path: Path, entries: list[dict], dry_run: bool,
                  stats: dict) -> list[str]:
    """Procesa un lote de métodos: UNA llamada grande con todos (aprovechando el
    output amplio de DeepSeek) y reintentos dirigidos de los que fallan, pasando
    al LLM el error exacto. Después cada método con candidatos se decide y aplica
    con los criterios habituales (select_best, log). Devuelve el estado por método."""
    data, usage = _batch_call(client, entries, stats)
    if data is None:
        # Fallo total del lote: partir por la mitad y reintentar cada parte.
        if len(entries) > 1:
            mid = len(entries) // 2
            return (process_batch(client, work_path, entries[:mid], dry_run, stats)
                    + process_batch(client, work_path, entries[mid:], dry_run, stats))
        for e in entries:
            stats["llm_errors"] += 1
            log_entry(project=e["project"], file=e["rel_file"], start_line=e["start_line"],
                      signature=e["signature"], base_cc=e["base_cc"],
                      reason="el batch no respondió JSON válido (incl. en solitario)",
                      status="llm_error", valid=False)
        return ["llm_error"]

    project = entries[0]["project"]
    batch_tokens = (usage or {}).get("total_tokens", 0)
    log_entry(event="llm_batch", project=project, methods=len(entries),
              tokens=batch_tokens)

    # Evaluación inicial: separar métodos OK de fallidos (con su error)
    results = []    # (entry, candidates)
    failed = []     # (entry, error)
    by_id = _group_refactors(data)
    for i, e in enumerate(entries):
        cands, err = _evaluate_batch_method(work_path, e, by_id.get(i, []),
                                            batch_tokens, stats)
        if cands:
            results.append((e, cands))
        else:
            failed.append((e, err))

    # Reintentos dirigidos con feedback del error exacto
    fixed, still_failed = _retry_batch(client, work_path, failed, batch_tokens,
                                       stats, REFACTOR_BATCH_RETRIES)
    results.extend(fixed)

    # Métodos que siguen fallando: llm_error (NO final; se reintentarán al reanudar)
    for e, err in still_failed:
        stats["llm_errors"] += 1
        log_entry(project=e["project"], file=e["rel_file"], start_line=e["start_line"],
                  signature=e["signature"], base_cc=e["base_cc"],
                  reason=f"falló tras reintentos: {err[:200]}",
                  status="llm_error", valid=False)

    # Decidir y aplicar cada método con candidatos (mismo criterio que stream)
    statuses = []
    for e, candidates in results:
        statuses.append(finalize_method(e["project"], work_path, e["rel_file"],
                                        e["start_line"], e["signature"], e["locate_sig"],
                                        e["base_cc"], e["base_cyclo"], candidates,
                                        dry_run, stats))
    return statuses


def main():
    parser = argparse.ArgumentParser(description="Bucle de refactorización con LLM")
    parser.add_argument("--project", help="Proyecto concreto (carpeta en projects/)")
    parser.add_argument("--limit", type=int, help="Máximo de métodos a procesar")
    parser.add_argument("--dry-run", action="store_true",
                        help="No deja cambios permanentes ni commits en las copias")
    parser.add_argument("--fresh", action="store_true",
                        help="Ignora la reanudación (borra el log y reprocesa todo)")
    parser.add_argument("--log", help="Fichero de log de salida (default data/refactor_log.jsonl)")
    parser.add_argument("--resume", help="Fichero del que leer los métodos ya hechos para "
                        "reanudar (default: el mismo que --log)")
    parser.add_argument("--quiet", action="store_true",
                        help="Sin línea de progreso en vivo (usar en ejecuciones paralelas)")
    args = parser.parse_args()

    if args.log:
        global LOG_PATH
        LOG_PATH = Path(args.log)
    resume_path = Path(args.resume) if args.resume else LOG_PATH

    client = llm_client.LLMClient()
    if not client.api_key:
        print("ERROR: LLM_API_KEY no definida en .env")
        return

    print("Config:")
    print(f"  WORK_DIR={WORK_DIR} CC_THRESHOLD={CC_THRESHOLD} "
          f"CYCLO_PENALTY={CYCLO_PENALTY} MAX_CYCLO_DELTA_PCT={MAX_CYCLO_DELTA_PCT} "
          f"SEED={SEED} REFACTOR_MAX_TOKENS={REFACTOR_MAX_TOKENS} RUN_TESTS={RUN_TESTS}")
    print(f"  MODO={REFACTOR_MODE}"
          + (f" BATCH_MAX_TOKENS={REFACTOR_BATCH_MAX_TOKENS}" if REFACTOR_MODE == "batch" else ""))
    print(f"  LLM: {client.base_url}  model={client.model}  "
          f"reasoning_effort={client.reasoning_effort}")
    print(f"  LOG={LOG_PATH}")

    if args.fresh and LOG_PATH.exists():
        LOG_PATH.unlink()
        print("[fresh] log reiniciado")

    own_projects = set(args.project.split(",")) if args.project else None
    pairs = workspace.ensure_workspace(PROJECTS_DIR, WORK_DIR, only=own_projects)
    work_map = {name: path for name, path in pairs}

    # Limpiar restos de una ejecución interrumpida (archivos modificados sin commit).
    # IMPORTANTE (paralelismo): solo se restauran los proyectos que ESTE proceso va a
    # procesar; si se restaurasen todos, un worker desharía los commits a medio hacer
    # de otro worker en proyectos ajenos.
    for name, path in pairs:
        if own_projects is not None and name not in own_projects:
            continue
        st = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                            capture_output=True, text=True).stdout.strip()
        if st:
            print(f"[limpieza] {name}: se restauran {len(st.splitlines())} archivos "
                  f"modificados (ejecución anterior interrumpida)")
            subprocess.run(["git", "-C", str(path), "restore", "."], check=True)

    stats = {k: 0 for k in [
        "skipped_no_file", "skipped_no_technique", "skipped_not_found",
        "candidates", "no_valid_candidate", "kept_original", "kept_committed",
        "kept_dry_run", "apply_failed", "methods_refactored", "llm_errors",
        "batch_retries",
    ]}

    csvs = sorted(DATA_DIR.glob("final_methods_dataset_*.csv"))
    if args.project:
        names = {f"final_methods_dataset_{p}.csv" for p in args.project.split(",")}
        csvs = [c for c in csvs if c.name in names]

    done_keys = set() if args.fresh else load_done_keys(resume_path)
    if done_keys:
        print(f"[resume] {len(done_keys)} métodos ya procesados; se reanudará desde ahí")

    total = sum(len(pd.read_csv(c)) for c in csvs)
    labeled_total = sum(int((pd.read_csv(c)[TARGETS].sum(axis=1) > 0).sum()) for c in csvs)
    progress = Progress(total, labeled_total, quiet=args.quiet)

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
        pending = []
        for _, row in df.iterrows():
            if args.limit and total_processed >= args.limit:
                break
            key = (project, str(row["file"]), int(row["method_start_line"]))
            if key in done_keys:
                progress.update("reanudado", labeled=False, resumed=True)
                total_processed += 1
                continue
            pending.append((key, row))
            total_processed += 1

        if REFACTOR_MODE == "batch":
            entries = []
            for key, row in pending:
                ent, err = batch_entry(project, work_path, row)
                if ent is None:
                    status = err
                    if err == "no_technique":
                        stats["skipped_no_technique"] += 1
                    elif err == "no_file":
                        stats["skipped_no_file"] += 1
                    else:
                        stats["skipped_not_found"] += 1
                    log_entry(project=project, file=str(row["file"]),
                              start_line=int(row["method_start_line"]),
                              reason="método no localizado en el proyecto original"
                              if err == "not_found" else None,
                              status=status, valid=err != "no_technique")
                    done_keys.add(key)
                    progress.update(status,
                                    labeled=status not in ("no_file", "no_technique", "not_found"))
                    continue
                entries.append(ent)
                # Trocear por presupuesto de tokens (dejamos siempre al menos 1)
                if len(entries) > 1 and _batch_tokens(entries) > REFACTOR_BATCH_MAX_TOKENS * 0.8:
                    chunk, entries = entries[:-1], [entries[-1]]
                    for ent2, st in zip(chunk, process_batch(client, work_path, chunk,
                                                             args.dry_run, stats)):
                        key2 = (project, ent2["rel_file"], ent2["start_line"])
                        done_keys.add(key2)
                        progress.update(st, labeled=True)
            if entries:
                for ent2, st in zip(entries, process_batch(client, work_path, entries,
                                                           args.dry_run, stats)):
                    key2 = (project, ent2["rel_file"], ent2["start_line"])
                    done_keys.add(key2)
                    progress.update(st, labeled=True)
        else:
            for key, row in pending:
                status = process_method(project, work_path, row, client, args.dry_run, stats)
                done_keys.add(key)  # checkpoint en memoria ante cortes
                progress.update(status, labeled=status not in ("no_file", "no_technique", "not_found"))

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

    progress.finish()
    print("\n--- Resumen ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"Log: {LOG_PATH}")
    print("Para reanudar una ejecución interrumpida, vuelve a ejecutar el mismo comando "
          "(los métodos ya procesados se saltan).")


if __name__ == "__main__":
    main()