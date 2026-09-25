"""Tests del wrapper Python del analizador Java (subprocess simulado)."""

import json
import subprocess
import unittest
from unittest import mock

import pandas as pd

from lib import ast_analyzer as ast
from tests.support import TempDirTestCase


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["java"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class AnalyzeMethodTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.java_file = self.tmp / "A.java"
        self.java_file.write_text("class A {}\n", encoding="utf-8")
        self.addCleanup(setattr, ast, "last_error", ast.last_error)
        ast.last_error = None

    def test_success_returns_metrics(self):
        payload = {"method_name": "m", "cognitive_complexity": 3}
        with mock.patch.object(ast.subprocess, "run",
                               return_value=completed(0, json.dumps(payload))) as run:
            result = ast.analyze_method(str(self.java_file), 4)
        self.assertEqual(result, payload)
        self.assertIsNone(ast.last_error)
        self.assertEqual(run.call_args.args[0][3:], [str(self.java_file), "4"])

    def test_missing_file_returns_none_without_running(self):
        with mock.patch.object(ast.subprocess, "run") as run:
            self.assertIsNone(ast.analyze_method(str(self.tmp / "nope.java"), 1))
        run.assert_not_called()

    def test_java_missing_returns_none(self):
        with mock.patch.object(ast.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(ast.analyze_method(str(self.java_file), 1))

    def test_parse_error_sets_last_error(self):
        stderr = "warning: algo\nParse error at line 3: esperado ';'\n"
        with mock.patch.object(ast.subprocess, "run",
                               return_value=completed(1, "", stderr)):
            self.assertIsNone(ast.analyze_method(str(self.java_file), 1))
        self.assertEqual(ast.last_error, "Parse error at line 3: esperado ';'")

    def test_invalid_json_returns_none(self):
        with mock.patch.object(ast.subprocess, "run", return_value=completed(0, "no-json")):
            self.assertIsNone(ast.analyze_method(str(self.java_file), 1))
        self.assertIsNone(ast.last_error)

    def test_signature_uses_signature_anchor(self):
        with mock.patch.object(ast.subprocess, "run",
                               return_value=completed(0, "{}")) as run:
            ast.analyze_method_by_signature(str(self.java_file), "C.m(int)")
        self.assertEqual(run.call_args.args[0][3:], [str(self.java_file), "C.m(int)"])


class ErrorLineTests(unittest.TestCase):
    def test_prefers_parse_error(self):
        stderr = "info\nLexical error at 2\n"
        self.assertEqual(ast._first_error_line(stderr), "Lexical error at 2")

    def test_fallback_returns_whole_stderr_stripped(self):
        self.assertEqual(ast._first_error_line("\nprimera\nsegunda"), "primera\nsegunda")

    def test_truncates(self):
        self.assertEqual(len(ast._first_error_line("x" * 500)), 400)


class ScanProjectTests(TempDirTestCase):
    def test_success_returns_dataframe(self):
        data = [{"file": "A.java", "method_name": "m", "cognitive_complexity": 20}]
        with mock.patch.object(ast.subprocess, "run",
                               return_value=completed(0, json.dumps(data))) as run:
            df = ast.scan_project_complex_methods(str(self.tmp), threshold=15)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 1)
        self.assertEqual(run.call_args.args[0][3:], ["scan", str(self.tmp), "15"])

    def test_failure_returns_empty_dataframe(self):
        with mock.patch.object(ast.subprocess, "run",
                               return_value=completed(1, "", "boom")):
            df = ast.scan_project_complex_methods(str(self.tmp))
        self.assertTrue(df.empty)

    def test_invalid_json_returns_empty_dataframe(self):
        with mock.patch.object(ast.subprocess, "run", return_value=completed(0, "{")):
            df = ast.scan_project_complex_methods(str(self.tmp))
        self.assertTrue(df.empty)


if __name__ == "__main__":
    unittest.main()
