"""
Panel de progreso en una terminal aparte, activado por la variable SHOW_TERMINAL.

Cuando `SHOW_TERMINAL` es verdadero, `run_parallel.py` o `refactor_loop.py` llaman
a `maybe_launch_terminal`, que abre una consola nueva ejecutando este módulo. El
panel se refresca cada pocos segundos, agrega los logs de los workers
(`<DATA_DIR>/refactor_log_w*.jsonl`) o, en ejecución secuencial, el log principal,
y se cierra cuando el proceso vigilado (PID) termina.

Uso directo (normalmente lo lanza el propio pipeline):
    python lib/progress_monitor.py --pid <PID> [--data-dir data]
                                   [--main-log data/refactor_log.jsonl]
                                   [--stdout-log <fichero>]
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

TRUTHY = {"1", "true", "yes", "on"}


def _pid_alive(pid: int) -> bool:
    """True si el proceso `pid` sigue vivo (sin lanzar señales)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_counts(path: Path) -> tuple[int, int]:
    """(nº de líneas, nº de entradas con status 'kept') del log."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, 0
    return len(text.splitlines()), text.count('"status": "kept"')


def _tail(path: Path, n: int = 6) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _set_title(title: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


def run(pid: int, data_dir: Path, main_log: Path, stdout_log: Path | None) -> None:
    _set_title("Refactor LLM - progreso")
    t0 = time.time()
    seen_alive = False
    polls = 0

    while True:
        worker_logs = sorted(data_dir.glob("refactor_log_w*.jsonl"))
        total = kept = 0
        detail: list[str] = []
        if worker_logs:
            for wl in worker_logs:
                lines, k = _read_counts(wl)
                total += lines
                kept += k
                detail.append("  %-24s %6d líneas,  kept~%d" % (wl.name, lines, k))
        elif main_log.is_file():
            total, kept = _read_counts(main_log)
            detail.append("  %-24s %6d líneas,  kept~%d" % (main_log.name, total, kept))

        alive = _pid_alive(pid)
        if alive:
            seen_alive = True
        else:
            polls += 1

        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")

        print("=" * 56)
        print("  REFACTOR LLM  -  progreso del bucle")
        print("=" * 56)
        print("  Tiempo transcurrido : %s" % time.strftime("%H:%M:%S", time.gmtime(time.time() - t0)))
        print("  Proceso vigilado    : %d (%s)" % (pid, "activo" if alive else "terminado"))
        print("  Total líneas de log : %d" % total)
        print("  Refactors kept      : %d" % kept)
        print()
        for line in detail or ["  (esperando a que arranque el pipeline...)"]:
            print(line)
        print()
        print("  --- últimas líneas ---")
        if stdout_log and stdout_log.is_file():
            tail_src = stdout_log
        elif main_log.is_file():
            tail_src = main_log
        elif worker_logs:
            tail_src = max(worker_logs, key=lambda p: p.stat().st_mtime)
        else:
            tail_src = main_log
        for line in _tail(tail_src, 6):
            print("  " + line)
        print()

        # Salir cuando el proceso vigilado termina (con una pequeña tolerancia si
        # el panel arranca después de que el proceso ya haya acabado).
        if not alive and (seen_alive or polls >= 3):
            print("=" * 56)
            print("  === PROCESO TERMINADO ===")
            print("=" * 56)
            print("  Log principal : %s" % main_log)
            if stdout_log:
                print("  Salida        : %s" % stdout_log)
            print()
            try:
                input("  Pulsa Enter para cerrar...")
            except EOFError:
                pass
            return

        time.sleep(5)


def maybe_launch_terminal(data_dir, main_log, stdout_log=None):
    """Abre el panel en una consola nueva si SHOW_TERMINAL es verdadero.

    Devuelve el proceso lanzado (o None). Se apoya en `sys.executable`, por lo
    que el panel hereda el mismo intérprete/entorno virtual.
    """
    if os.getenv("SHOW_TERMINAL", "0").strip().lower() not in TRUTHY:
        return None
    script = Path(__file__).resolve()
    args = [sys.executable, str(script), "--pid", str(os.getpid()),
            "--data-dir", str(Path(data_dir).resolve()),
            "--main-log", str(Path(main_log).resolve())]
    if stdout_log:
        args += ["--stdout-log", str(Path(stdout_log).resolve())]

    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        return subprocess.Popen(args, creationflags=flags)
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["xterm", "-e"]):
        try:
            return subprocess.Popen(term + args)
        except FileNotFoundError:
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Panel de progreso del bucle de refactorización")
    parser.add_argument("--pid", type=int, required=True, help="PID del proceso vigilado")
    parser.add_argument("--data-dir", default="data", help="Directorio con los logs de workers")
    parser.add_argument("--main-log", default=None, help="Log principal (def: <data-dir>/refactor_log.jsonl)")
    parser.add_argument("--stdout-log", default=None, help="Fichero de salida estándar a mostrar (opcional)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    main_log = Path(args.main_log) if args.main_log else data_dir / "refactor_log.jsonl"
    stdout_log = Path(args.stdout_log) if args.stdout_log else None
    try:
        run(args.pid, data_dir, main_log, stdout_log)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
