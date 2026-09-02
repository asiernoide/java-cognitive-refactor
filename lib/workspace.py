"""
Workspace de refactor: copias de trabajo de los proyectos en out/.

Cada proyecto de projects/ se clona localmente (git clone --local, usa
hardlinks para los objetos de git) en WORK_DIR/<proyecto>. El refactor se
aplica sobre la copia; projects/ queda intacto con sus referencias previas.
La copia conserva su .git, por lo que se puede restaurar (git restore/reset)
sin regenerar nada.

Config (.env):
    WORK_DIR   directorio de trabajo (default 'out')

Uso:
    from lib import workspace
    pairs = workspace.ensure_workspace("projects")   # [(nombre, Path), ...]
    workspace.git_restore(pairs[0][1], "ruta/relativa.java")
    workspace.git_commit(pairs[0][1], "refactor: ...")
"""

import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

WORK_DIR = os.getenv("WORK_DIR", "out")


def _has_commits(repo: Path) -> bool:
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    return r.returncode == 0


def _git_init_and_initial_commit(repo: Path) -> None:
    """Inicializa git en una copia literal y crea un commit inicial, para que
    el restore por git funcione aunque el proyecto original no tuviera historial."""
    subprocess.run(["git", "init"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.name=tfm-refactor", "-c", "user.email=tfm@local",
         "commit", "-m", "initial snapshot (sin historial previo)"],
        cwd=str(repo),
        check=True,
    )


def ensure_workspace(projects_dir: str, work_dir: str | None = None) -> list[tuple[str, Path]]:
    """
    Garantiza una copia de trabajo (clon local) de cada proyecto en work_dir.
    Idempotente: si la copia ya existe y tiene .git con contenido, la reutiliza.
    Si el proyecto original no tiene historial git, se copia literalmente y se
    inicializa git con un commit inicial (restore por git igualmente posible).
    Devuelve [(nombre_proyecto, ruta_copia), ...].
    """
    projects_dir = Path(projects_dir)
    work_dir = Path(work_dir or WORK_DIR)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not projects_dir.is_dir():
        raise FileNotFoundError(f"No existe el directorio de proyectos: {projects_dir}")

    result = []
    for proj in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        dst = work_dir / proj.name

        # Reutilizar si la copia ya es válida (con git y contenido)
        if dst.exists() and (dst / ".git").exists() and _has_commits(dst):
            result.append((proj.name, dst))
            continue

        if dst.exists():
            print(f"[WARN] {dst} inválida; se elimina y se vuelve a crear")
            shutil.rmtree(dst)

        if (proj / ".git").exists() and _has_commits(proj):
            print(f"Clonando {proj.name} -> {dst}")
            subprocess.run(["git", "clone", "--local", str(proj), str(dst)], check=True)
        else:
            print(f"[WARN] {proj.name} sin historial git; copia literal + git init")
            shutil.copytree(proj, dst)
            _git_init_and_initial_commit(dst)

        result.append((proj.name, dst))
    return result


def git_restore(work_dir: str | Path, relative_file: str) -> None:
    """Restaura un archivo de la copia de trabajo a su último commit."""
    rel = relative_file.replace("\\", "/")  # git acepta '/' en Windows
    subprocess.run(["git", "restore", "--", rel], cwd=str(work_dir), check=True)


def git_commit(work_dir: str | Path, message: str) -> None:
    """Commitea todos los cambios de la copia de trabajo (commit local)."""
    subprocess.run(["git", "add", "-A"], cwd=str(work_dir), check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(work_dir), check=True)


if __name__ == "__main__":
    pairs = ensure_workspace(os.getenv("PROJECTS_DIR", "projects"))
    print(f"\nWorkspace listo ({len(pairs)} proyectos) en {WORK_DIR}:")
    for name, path in pairs:
        print(f"  {name}: {path}")