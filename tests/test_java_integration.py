"""Tests de integración del analizador Java real (ast-analyzer.jar).

Requieren java en el PATH y el JAR compilado; si no están, se omiten.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support import FIXTURES, JAR, TempDirTestCase, requires_java


def line_of(java_path: Path, needle: str) -> int:
    for number, line in enumerate(java_path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return number
    raise AssertionError(f"no se encontró {needle!r} en {java_path}")


@requires_java
class AnalyzerCliTests(TempDirTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(__file__).resolve().parent / "fixtures" / "scan_project"
        cls.java_file = FIXTURES / "scan_project" / "src" / "main" / "java" / "com" / "example" / "Complex.java"

    def run_jar(self, *args):
        return subprocess.run(["java", "-jar", str(JAR), *args],
                              capture_output=True, text=True)

    def analyze(self, method_needle):
        line = line_of(self.java_file, method_needle)
        result = self.run_jar(str(self.java_file), str(line))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_cognitive_complexity_values(self):
        expected = {
            "public int simpleIf": 1,
            "public int ifElse": 2,
            "public int elseIfChain": 2,
            "public int loopWithIf": 3,
            "public boolean logicalAnd": 1,
            "public int ternary": 1,
            "public int plain": 0,
            "public String lambdaNesting": 2,
            "public int switchCases": 1,
            "public int tryCatch": 1,
            "public boolean equals": 2,
            "public int hashCode": 1,
            "public Runnable anonymousClass": 2,
            "public int veryComplex": 20,
        }
        for needle, cc in expected.items():
            with self.subTest(method=needle):
                self.assertEqual(self.analyze(needle)["cognitive_complexity"], cc)

    def test_cyclomatic_complexity_values(self):
        expected = {
            "public int simpleIf": 2,
            "public int loopWithIf": 3,
            "public boolean logicalAnd": 3,
            "public int ternary": 2,
            "public int switchCases": 3,
            "public int tryCatch": 2,
            "public int plain": 1,
        }
        for needle, cyclo in expected.items():
            with self.subTest(method=needle):
                self.assertEqual(self.analyze(needle)["cyclomatic_complexity"], cyclo)

    def test_structural_metrics(self):
        collapse = self.analyze("public void collapseCandidate")
        self.assertEqual(collapse["nested_if_chains"], 1)
        self.assertEqual(collapse["nested_if_chains_with_else"], 0)

        map_loop = self.analyze("public void mapCandidate")
        self.assertEqual(map_loop["map_candidate_loops"], 1)
        self.assertEqual(map_loop["filter_map_candidate_loops"], 0)
        self.assertEqual(map_loop["simple_foreach_count"], 1)

        filter_map = self.analyze("public void filterMapCandidate")
        self.assertEqual(filter_map["filter_map_candidate_loops"], 1)
        self.assertEqual(filter_map["simple_foreach_count"], 0)

        reduce = self.analyze("public int reduceCandidate")
        self.assertEqual(reduce["reduce_candidate_loops"], 1)
        self.assertEqual(reduce["simple_foreach_count"], 0)

        loop_with_if = self.analyze("public int loopWithIf")
        self.assertEqual(loop_with_if["simple_foreach_count"], 0)

    def test_identity_fields(self):
        metrics = self.analyze("public int simpleIf")
        self.assertEqual(metrics["method_name"], "simpleIf")
        self.assertEqual(metrics["method_signature"], "simpleIf(int)")
        self.assertEqual(metrics["enclosing_class"], "Complex")
        self.assertLess(metrics["method_start_line"], metrics["method_end_line"])

    def test_lookup_by_signature(self):
        line = line_of(self.java_file, "public int simpleIf")
        for signature in ["simpleIf(int)", "Complex.simpleIf(int)"]:
            with self.subTest(signature=signature):
                result = self.run_jar(str(self.java_file), signature)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["method_start_line"], line)

    def test_unknown_method_line_exit_code(self):
        result = self.run_jar(str(self.java_file), "999999")
        self.assertEqual(result.returncode, 2)
        self.assertIn("No se encontró ningún método", result.stderr)

    def test_unknown_signature_exit_code(self):
        result = self.run_jar(str(self.java_file), "noExiste(int)")
        self.assertEqual(result.returncode, 2)

    def test_unparseable_file_exit_code(self):
        bad = self.tmp / "Bad.java"
        bad.write_text("class { esto no es java", encoding="utf-8")
        result = self.run_jar(str(bad), "1")
        self.assertEqual(result.returncode, 3)

    def test_scan_filters_by_threshold(self):
        project = self.copy_fixture_project()
        result = self.run_jar("scan", str(project), "15")
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertEqual([m["method_name"] for m in found], ["veryComplex"])
        self.assertEqual(found[0]["file"],
                         "src/main/java/com/example/Complex.java")
        self.assertEqual(found[0]["cognitive_complexity"], 20)

    def test_scan_excludes_equals_hashcode_and_anonymous_class(self):
        project = self.copy_fixture_project()
        found = json.loads(self.run_jar("scan", str(project), "0").stdout)
        names = {m["method_name"] for m in found}
        self.assertNotIn("equals", names)
        self.assertNotIn("hashCode", names)
        self.assertNotIn("run", names)  # método de clase anónima
        self.assertIn("simpleIf", names)

    def test_scan_empty_above_threshold(self):
        project = self.copy_fixture_project()
        found = json.loads(self.run_jar("scan", str(project), "1000").stdout)
        self.assertEqual(found, [])

    def test_scan_detects_multi_module_sources_and_excludes_target(self):
        project = self.tmp / "multi"
        module = project / "moduleA"
        target_copy = module / "target" / "classes"
        (module / "src" / "main" / "java").mkdir(parents=True)
        target_copy.mkdir(parents=True)
        (module / "pom.xml").write_text("<project/>", encoding="utf-8")
        shutil.copy(self.java_file, module / "src" / "main" / "java" / "Complex.java")
        shutil.copy(self.java_file, target_copy / "Complex.java")

        result = self.run_jar("scan", str(project), "15")
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        files = [m["file"] for m in found]
        self.assertEqual(files, ["moduleA/src/main/java/Complex.java"])

    def test_scan_ant_layout_excludes_test_directory(self):
        project = self.tmp / "ant"
        main_src = project / "src" / "com" / "example"
        test_src = project / "src" / "test" / "com" / "example"
        main_src.mkdir(parents=True)
        test_src.mkdir(parents=True)
        (project / "build.xml").write_text("<project/>", encoding="utf-8")
        (main_src / "Main.java").write_text(
            "package com.example;\n"
            "public class Main {\n"
            "    public void run() {\n"
            "        for (String s : java.util.List.of(\"a\")) {\n"
            "            System.out.println(s);\n"
            "        }\n"
            "    }\n"
            "}\n", encoding="utf-8")
        (test_src / "MainTest.java").write_text(
            "package com.example;\n"
            "public class MainTest {\n"
            "    public int complex(int a, int b, int c) {\n"
            "        int r = 0;\n"
            "        if (a > 0) { r += 1; }\n"
            "        if (b > 0) { r += 1; }\n"
            "        if (c > 0) { r += 1; }\n"
            "        if (a > 1) { if (b > 1) { r += 2; } }\n"
            "        return r;\n"
            "    }\n"
            "}\n", encoding="utf-8")

        found = json.loads(self.run_jar("scan", str(project), "0").stdout)
        self.assertEqual([m["file"] for m in found], ["src/com/example/Main.java"])
        self.assertEqual(found[0]["simple_foreach_count"], 1)

    def test_scan_keeps_package_named_test_in_main_sources(self):
        project = self.tmp / "maven"
        pkg = project / "src" / "main" / "java" / "test"
        pkg.mkdir(parents=True)
        (project / "pom.xml").write_text("<project/>", encoding="utf-8")
        (pkg / "Edge.java").write_text(
            "package test;\n"
            "public class Edge {\n"
            "    public void run() {\n"
            "        for (String s : java.util.List.of(\"a\")) {\n"
            "            System.out.println(s);\n"
            "        }\n"
            "    }\n"
            "}\n", encoding="utf-8")

        found = json.loads(self.run_jar("scan", str(project), "0").stdout)
        self.assertIn("src/main/java/test/Edge.java", [m["file"] for m in found])

    def test_scan_missing_directory_fails(self):
        result = self.run_jar("scan", str(self.tmp / "nope"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no es un directorio", result.stderr)


if __name__ == "__main__":
    unittest.main()
