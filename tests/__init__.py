"""Tests del proyecto (ejecutar desde la raíz: python -m unittest discover -s tests -v)."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Aísla los tests de un .env real: load_dotenv() no sobreescribe estas variables.
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
os.environ.setdefault("PROJECTS_DIR", str(ROOT / "projects"))
