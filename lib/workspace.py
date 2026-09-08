"""
Workspace de refactor: copias de trabajo de los proyectos en out/ (WORK_DIR).
Cada proyecto se clona localmente; el refactor se aplica sobre la copia y
projects/ queda intacto. Uso: ensure_workspace, git_restore, git_commit.
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
    """Git init + commit inicial para poder restaurar copias sin historial previo."""
    subprocess.run(["git", "init"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.name=tfm-refactor", "-c", "user.email=tfm@local",
         "commit", "-m", "initial snapshot (sin historial previo)"],
        cwd=str(repo),
        check=True,
    )


def ensure_workspace(projects_dir: str, work_dir: str | None = None,
                     only: list[str] | set[str] | None = None) -> list[tuple[str, Path]]:
    """Clona (o reutiliza) cada proyecto en work_dir. `only` limita a unos proyectos.
    Devuelve [(nombre, ruta), ...]."""
    projects_dir = Path(projects_dir)
    work_dir = Path(work_dir or WORK_DIR)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not projects_dir.is_dir():
        raise FileNotFoundError(f"No existe el directorio de proyectos: {projects_dir}")

    only_set = set(only) if only else None
    result = []
    for proj in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        if only_set is not None and proj.name not in only_set:
            continue
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