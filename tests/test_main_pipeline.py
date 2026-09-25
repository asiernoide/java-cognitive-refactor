"""Test end-to-end de la fase de detección (main.py) con el analizador real."""

import shutil
import unittest
from unittest import mock

import pandas as pd

import main as main_module
from tests.support import FIXTURES, JAR, TempDirTestCase, requires_java


@requires_java
class MainPipelineTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.projects = self.tmp / "projects"
        self.data = self.tmp / "data"
        self.projects.mkdir()
        shutil.copytree(FIXTURES / "scan_project", self.projects / "proj")
        self.patches = [
            mock.patch.object(main_module, "PROJECTS_DIR", str(self.projects)),
            mock.patch.object(main_module, "DATA_DIR", str(self.data)),
            mock.patch.object(main_module.ast, "JAVA_ANALYZER_JAR", str(JAR)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_generates_csv_with_only_complex_methods(self):
        main_module.main()
        csv = self.data / "final_methods_dataset_proj.csv"
        self.assertTrue(csv.exists())
        df = pd.read_csv(csv)
        self.assertEqual(list(df["method_name"]), ["veryComplex"])
        self.assertTrue((df["cognitive_complexity"] > 15).all())
        self.assertIn("enclosing_class", df.columns)

    def test_threshold_above_all_methods_writes_nothing(self):
        with mock.patch.object(main_module, "CC_THRESHOLD", 1000):
            main_module.main()
        self.assertFalse((self.data / "final_methods_dataset_proj.csv").exists())

    def test_missing_projects_dir_returns_gracefully(self):
        with mock.patch.object(main_module, "PROJECTS_DIR", str(self.tmp / "nope")):
            main_module.main()
        self.assertFalse(self.data.exists())

    def test_multiple_projects_are_processed(self):
        shutil.copytree(FIXTURES / "refactor_project", self.projects / "loop")
        main_module.main()
        self.assertTrue((self.data / "final_methods_dataset_proj.csv").exists())
        self.assertFalse((self.data / "final_methods_dataset_loop.csv").exists())


if __name__ == "__main__":
    unittest.main()
