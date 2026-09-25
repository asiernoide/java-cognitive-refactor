"""Lanzador paralelo del bucle de refactorización.

Reparte los proyectos entre N workers (cada uno en su propio proceso y log) y
fusiona los logs parciales en el principal. Cada proyecto lo procesa un solo
worker (sin conflictos git) y cada worker usa un OPENCODE_SESSION_ID distinto.
Con SHOW_TERMINAL=1 abre una consola aparte con el panel de progreso.

Uso:
    python run_parallel.py -n 4 [--fresh] [--dry-run --limit N] [--resume F]
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from lib import progress_monitor  # noqa: E402

load_dotenv()


def resolve_dir(env_name: str, default: str) -> Path:
    """Directorio de configuración (DATA_DIR/PROJECTS_DIR), relativo al repo si
    no es absoluto; así el reparto de workers respeta el .env igual que el bucle."""
    path = Path(os.getenv(env_name, default))
    return path if path.is_absolute() else ROOT / path


DATA = resolve_dir("DATA_DIR", "data")
PROJECTS = resolve_dir("PROJECTS_DIR", "projects")
MAIN_LOG = DATA / "refactor_log.jsonl"
WORKER_LOG_PATTERN = "refactor_log_w*.jsonl"


def project_groups(workers: int) -> list[list[str]]:
    projects = sorted(p.name for p in PROJECTS.iterdir() if p.is_dir())
    groups: list[list[str]] = [[] for _ in range(workers)]
    for i, p in enumerate(projects):
        groups[i % workers].append(p)
    return [g for g in groups if g]


def merge_worker_logs() -> int:
    """Fusiona los logs de worker en el principal y los borra; devuelve nº de entradas."""
    worker_logs = sorted(DATA.glob(WORKER_LOG_PATTERN))
    if not worker_logs:
        return 0
    entries = []
    for wl in worker_logs:
        for line in wl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    entries.sort(key=lambda e: e.get("timestamp", ""))
    with MAIN_LOG.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    for wl in worker_logs:
        wl.unlink()
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description="Lanzador paralelo del bucle de refactorización")
    parser.add_argument("-n", "--workers", type=int, default=2, help="Número de workers (default 2)")
    parser.add_argument("--fresh", action="store_true",
                        help="Borra el log principal antes de empezar (ignora reanudación)")
    parser.add_argument("--dry-run", action="store_true", help="No deja cambios ni commits")
    parser.add_argument("--limit", type=int, default=None,
                        help="Máximo de métodos por worker (pruebas)")
    parser.add_argument("--projects", help="Solo estos proyectos (comma-separated)")
    parser.add_argument("--resume", default=str(MAIN_LOG),
                        help="Fichero con los métodos ya hechos (reanudación)")
    args = parser.parse_args()

    if args.fresh:
        if MAIN_LOG.exists():
            MAIN_LOG.unlink()
        for wl in DATA.glob(WORKER_LOG_PATTERN):
            wl.unlink()
        print("[fresh] logs (principal y de worker) reiniciados")

    merged = merge_worker_logs()
    if merged:
        print(f"[resume] fusionadas {merged} entradas de logs de worker anteriores")

    groups = project_groups(max(1, args.workers))
    if args.projects:
        sel = set(args.projects.split(","))
        groups = [[p for p in g if p in sel] for g in groups]
        groups = [g for g in groups if g]
    if not groups:
        print("No hay proyectos que procesar.")
        return

    print(f"{len(groups)} workers:")
    for i, g in enumerate(groups):
        print(f"  worker {i}: {', '.join(g)}")

    # Panel de progreso en una consola aparte (si SHOW_TERMINAL=1)
    progress_monitor.maybe_launch_terminal(DATA, MAIN_LOG)

    procs = []
    for idx, group in enumerate(groups):
        worker_log = DATA / f"refactor_log_w{idx}.jsonl"
        cmd = [sys.executable, str(ROOT / "refactor_loop.py"),
               "--project", ",".join(group),
               "--log", str(worker_log),
               "--resume", args.resume,
               "--quiet"]
        if args.fresh:
            cmd.append("--fresh")
        if args.dry_run:
            cmd.append("--dry-run")
        if args.limit:
            cmd.extend(["--limit", str(args.limit)])
        env = os.environ.copy()
        env["OPENCODE_SESSION_ID"] = str(uuid.uuid4())
        print(f"  lanzando worker {idx}: {' '.join(cmd)}")
        procs.append((idx, subprocess.Popen(cmd, cwd=str(ROOT), env=env)))

    codes = {}
    for idx, p in procs:
        p.wait()
        codes[idx] = p.returncode
        print(f"  worker {idx} terminado (exit={p.returncode})")

    n = merge_worker_logs()
    bad = [idx for idx, c in codes.items() if c != 0]
    print("--- Fin paralelo ---")
    print(f"workers con error: {bad if bad else 'ninguno'} | entradas fusionadas: {n}")
    print(f"log principal: {MAIN_LOG}")


if __name__ == "__main__":
    main()