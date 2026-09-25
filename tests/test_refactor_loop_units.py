"""Tests unitarios de las utilidades de refactor_loop."""

import json
import random
import unittest
from pathlib import Path
from unittest import mock

import refactor_loop as rl
from tests.support import TempDirTestCase


class CleanCodeTests(unittest.TestCase):
    def test_plain_code_unchanged(self):
        code = "public void m() {\n    int a = 1;\n}"
        self.assertEqual(rl.clean_code(code), code)

    def test_strips_markdown_fence(self):
        code = "```java\npublic void m() { return; }\n```"
        self.assertEqual(rl.clean_code(code), "public void m() { return; }")

    def test_strips_lone_java_language_line(self):
        code = "```\njava\npublic void m() {\n}\n```"
        self.assertEqual(rl.clean_code(code), "public void m() {\n}")

    def test_adds_missing_closing_brace(self):
        self.assertTrue(rl.clean_code("public void m() {").endswith("}"))

    def test_removes_surrounding_blank_lines(self):
        self.assertEqual(rl.clean_code("\n\npublic int x() { return 1; }\n"),
                         "public int x() { return 1; }")


class ReindentTests(unittest.TestCase):
    def test_reindents_to_base(self):
        out = rl.reindent("public void m() {\n    int a = 1;\n}", "  ")
        self.assertEqual(out, ["  public void m() {", "      int a = 1;", "  }"])

    def test_uses_first_non_empty_line_as_reference(self):
        out = rl.reindent("    public void m() {\n        int a = 1;\n    }", "")
        self.assertEqual(out, ["public void m() {", "    int a = 1;", "}"])

    def test_preserves_blank_lines(self):
        out = rl.reindent("public void m() {\n\n    int a = 1;\n}", "")
        self.assertEqual(out, ["public void m() {", "", "    int a = 1;", "}"])


class ApplyCandidateTests(TempDirTestCase):
    def _write(self, content, eol="\n"):
        path = self.tmp / "T.java"
        path.write_text(content, encoding="utf-8", newline="")
        return path

    def test_replaces_method_and_keeps_lf(self):
        path = self._write("class T {\n    void a() {\n        int x = 1;\n    }\n}\n")
        ok = rl.apply_candidate(path, {"method_start_line": 2, "method_end_line": 4},
                                "void a() {\n    int x = 2;\n}", [])
        self.assertTrue(ok)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "class T {\n    void a() {\n        int x = 2;\n    }\n}\n")
        self.assertNotIn(b"\r\n", path.read_bytes())

    def test_inserts_new_methods_after(self):
        path = self._write("class T {\n    void a() {\n        int x = 1;\n    }\n}\n")
        rl.apply_candidate(path, {"method_start_line": 2, "method_end_line": 4},
                           "void a() {\n    int x = 2;\n}",
                           ["private void h() {\n    int y = 1;\n}"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("private void h() {", text)
        self.assertLess(text.index("void a()"), text.index("private void h()"))

    def test_preserves_crlf(self):
        path = self._write("class T {\r\n    void a() {\r\n    }\r\n}\r\n")
        rl.apply_candidate(path, {"method_start_line": 2, "method_end_line": 3},
                           "void a() {\n    int x = 1;\n}", [])
        self.assertIn(b"\r\n", path.read_bytes())

    def test_out_of_range_returns_false(self):
        path = self._write("class T {}\n")
        self.assertFalse(rl.apply_candidate(path, {"method_start_line": 50, "method_end_line": 60},
                                            "x", []))
        self.assertEqual(path.read_text(encoding="utf-8"), "class T {}\n")


class SelectBestTests(unittest.TestCase):
    def _candidate(self, technique, cc, cyclo):
        return {"technique": technique, "cc": cc, "cyclo": cyclo,
                "loc": 1, "invocations": 1, "tokens": 0}

    def setUp(self):
        self.addCleanup(setattr, rl, "CYCLO_PENALTY", rl.CYCLO_PENALTY)
        self.addCleanup(setattr, rl, "RNG", rl.RNG)
        rl.RNG = random.Random(42)

    def test_picks_highest_score(self):
        rl.CYCLO_PENALTY = 1.0
        cands = [self._candidate("a", 5, 1), self._candidate("b", 3, 1)]
        best, score = rl.select_best(cands, 10, 2)
        self.assertEqual(best["technique"], "b")
        self.assertAlmostEqual(score, 7.0)

    def test_penalty_reduces_score(self):
        rl.CYCLO_PENALTY = 1.0
        cands = [self._candidate("a", 5, 6), self._candidate("b", 4, 1)]
        best, score = rl.select_best(cands, 10, 2)
        self.assertEqual(best["technique"], "b")
        self.assertAlmostEqual(score, 6.0)

    def test_tie_goes_to_lower_cyclomatic(self):
        rl.CYCLO_PENALTY = 0.0
        cands = [self._candidate("a", 5, 3), self._candidate("b", 5, 2)]
        best, _ = rl.select_best(cands, 10, 2)
        self.assertEqual(best["technique"], "b")

    def test_deterministic_with_seed(self):
        cands = [self._candidate("a", 5, 2), self._candidate("b", 5, 2)]
        rl.RNG = random.Random(42)
        best1, _ = rl.select_best([dict(c) for c in cands], 10, 2)
        rl.RNG = random.Random(42)
        best2, _ = rl.select_best([dict(c) for c in cands], 10, 2)
        self.assertEqual(best1["technique"], best2["technique"])

    def test_no_candidates(self):
        best, score = rl.select_best([], 10, 2)
        self.assertIsNone(best)
        self.assertEqual(score, 0.0)

    def test_stores_rounded_score_in_candidate(self):
        cands = [self._candidate("a", 5, 2)]
        rl.select_best(cands, 10, 1)
        self.assertEqual(cands[0]["score"], 4.0)


class LoadDoneKeysTests(TempDirTestCase):
    def test_only_settled_statuses_with_line(self):
        log = self.tmp / "log.jsonl"
        entries = [
            {"project": "p", "file": "A.java", "start_line": 10, "status": "kept"},
            {"project": "p", "file": "A.java", "start_line": 11, "status": "keep_original"},
            {"project": "p", "file": "A.java", "start_line": 12, "status": "no_technique"},
            {"project": "p", "file": "A.java", "start_line": 13, "status": "no_file"},
            {"project": "p", "file": "A.java", "start_line": 14, "status": "llm_error"},
            {"project": "p", "file": "A.java", "start_line": 15, "status": "apply_failed"},
            {"project": "p", "file": "A.java", "status": "kept"},
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\nlinea corrupta\n",
                       encoding="utf-8")
        keys = rl.load_done_keys(log)
        self.assertEqual(keys, {("p", "A.java", 10), ("p", "A.java", 11),
                                ("p", "A.java", 12), ("p", "A.java", 13)})

    def test_missing_file_is_empty(self):
        self.assertEqual(rl.load_done_keys(self.tmp / "nope.jsonl"), set())


class MiscHelpersTests(unittest.TestCase):
    def test_techniques_for(self):
        row = {"refactor_extract_method": 1, "refactor_lambda_map": 0,
               "refactor_lambda_reduce": "1"}
        self.assertEqual(rl.techniques_for(row),
                         ["refactor_extract_method", "refactor_lambda_reduce"])

    def test_batch_tokens_estimate(self):
        entries = [{"source": "x" * 300, "techniques": ["a"]},
                   {"source": "y" * 301, "techniques": ["a", "b"]}]
        self.assertEqual(rl._batch_tokens(entries), (300 // 3 * 1 + 200) + (301 // 3 * 2 + 200))

    def test_group_refactors_ignores_invalid_ids(self):
        data = {"refactors": [{"id": 1, "x": 1}, {"id": 1, "x": 2},
                              {"id": "z"}, {"no_id": 1}, "texto"]}
        grouped = rl._group_refactors(data)
        self.assertEqual(len(grouped[1]), 2)
        self.assertEqual(list(grouped.keys()), [1])

    def test_group_refactors_none(self):
        self.assertEqual(rl._group_refactors(None), {})

    def test_looks_like_java_method(self):
        self.assertFalse(rl.looks_like_java_method(""))
        self.assertFalse(rl.looks_like_java_method("corto()"))
        self.assertTrue(rl.looks_like_java_method("public void m() {\n  int a = 1;\n}"))

    def test_locate_qualified_signature(self):
        with mock.patch.object(rl, "PROJECTS_DIR", str(self.tmp_dir())), \
             mock.patch.object(rl.ast, "analyze_method", return_value={
                 "method_signature": "m(int)", "enclosing_class": "Outer.Inner"}):
            located = rl.locate_original("proj", "A.java", 5)
        self.assertIsNotNone(located)
        _, sig, meta = located
        self.assertEqual(sig, "Outer.Inner.m(int)")
        self.assertEqual(meta["method_signature"], "m(int)")

    def test_locate_unqualified_signature(self):
        with mock.patch.object(rl, "PROJECTS_DIR", str(self.tmp_dir())), \
             mock.patch.object(rl.ast, "analyze_method", return_value={
                 "method_signature": "m(int)", "enclosing_class": ""}):
            located = rl.locate_original("proj", "A.java", 5)
        self.assertEqual(located[1], "m(int)")

    def test_locate_original_none(self):
        with mock.patch.object(rl, "PROJECTS_DIR", str(self.tmp_dir())), \
             mock.patch.object(rl.ast, "analyze_method", return_value=None):
            self.assertIsNone(rl.locate_original("proj", "A.java", 5))

    def tmp_dir(self):
        import shutil
        import tempfile
        path = Path(tempfile.mkdtemp(prefix="tfm_helper_"))
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def test_fmt_eta(self):
        self.assertEqual(rl.fmt_eta(0), "0h00m")
        self.assertEqual(rl.fmt_eta(3661), "1h01m")


class ProgressTests(unittest.TestCase):
    def test_counts_and_resumed(self):
        progress = rl.Progress(10, 4, checkpoint_every=100, quiet=True)
        progress.update("kept", labeled=True)
        progress.update("no_file")
        progress.update("reanudado", resumed=True)
        self.assertEqual(progress.counts, {"kept": 1, "no_file": 1})
        self.assertEqual(progress.resume_skipped, 1)
        self.assertEqual(progress.labeled_done, 1)
        self.assertEqual(progress.processed, 3)
        self.assertIn("kept=1", progress._counts_str())

    def test_eta_without_labeled(self):
        progress = rl.Progress(10, 0, quiet=True)
        self.assertEqual(progress._eta(), "?")


if __name__ == "__main__":
    unittest.main()
