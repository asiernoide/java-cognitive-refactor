"""Utilidades compartidas por la suite de tests."""

import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
JAR = ROOT / "ast-analyzer" / "ast-analyzer.jar"

HAVE_JAVA = shutil.which("java") is not None and JAR.is_file()
HAVE_GIT = shutil.which("git") is not None
requires_java = unittest.skipUnless(HAVE_JAVA, "requiere java + ast-analyzer.jar")
requires_git = unittest.skipUnless(HAVE_GIT, "requiere git")


class TempDirTestCase(unittest.TestCase):
    """TestCase con un directorio temporal propio, borrado al terminar."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tfm_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def copy_fixture_project(self, name: str = "scan_project") -> Path:
        """Copia un proyecto fixture a un directorio temporal y devuelve su raíz."""
        dst = self.tmp / name
        shutil.copytree(FIXTURES / name, dst)
        return dst
