"""Test end-to-end del bucle de refactor con LLM simulado y analizador Java real."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import refactor_loop as rl
from lib import workspace
from tests.support import JAR, TempDirTestCase, requires_git, requires_java

REFACTORED = (
    "public List<String> trimAll(List<String> input) {\n"
    "    return input.stream().map(String::trim)\n"
    "            .collect(java.util.stream.Collectors.toList());\n"
    "}"
)


class StubClient:
    """Cliente LLM simulado: devuelve siempre el mismo refactor (map)."""

    def __init__(self, response=REFACTORED):
        self.response = response
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(("chat", messages, kwargs))
        return self.response, {"total_tokens": 25}

    def chat_json(self, messages, **kwargs):
        self.calls.append(("chat_json", messages, kwargs))
        return {"main_method": self.response, "new_methods": []}, {"total_tokens": 25}


@requires_java
@requires_git
class RefactorLoopE2ETests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.projects = self.tmp / "projects"
        self.project = self.projects / "proj"
        import shutil
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "refactor_project",
                        self.project)
        self.rel_file = "src/main/java/com/example/Loop.java"
        source = (self.project / self.rel_file).read_text(encoding="utf-8")
        self.start_line = next(i for i, l in enumerate(source.splitlines(), 1)
                               if "public List<String> trimAll" in l)
        work = self.tmp / "work"
        pairs = workspace.ensure_workspace(str(self.projects), str(work))
        self.work_path = pairs[0][1]
        self.log_path = self.tmp / "refactor_log.jsonl"
        self.patches = [
            mock.patch.object(rl, "PROJECTS_DIR", str(self.projects)),
            mock.patch.object(rl, "LOG_PATH", self.log_path),
            mock.patch.object(rl.ast, "JAVA_ANALYZER_JAR", str(JAR)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def row(self, **overrides):
        base = {"file": self.rel_file, "method_start_line": self.start_line,
                "refactor_lambda_map": 1, "refactor_extract_method": 0,
                "refactor_collapse_ifs_with_and": 0, "refactor_lambda_filter_map": 0,
                "refactor_lambda_reduce": 0}
        base.update(overrides)
        return base

    def stats(self):
        return {k: 0 for k in ["skipped_no_file", "skipped_no_technique",
                               "skipped_not_found", "candidates", "no_valid_candidate",
                               "kept_original", "kept_committed", "kept_dry_run",
                               "apply_failed", "methods_refactored", "llm_errors",
                               "batch_retries"]}

    def test_refactor_is_applied_measured_and_committed(self):
        client = StubClient()
        stats = self.stats()
        status = rl.process_method("proj", self.work_path, self.row(), client,
                                   dry_run=False, stats=stats)
        self.assertEqual(status, "kept")
        self.assertEqual(stats["kept_committed"], 1)
        self.assertEqual(stats["candidates"], 1)

        text = (self.work_path / self.rel_file).read_text(encoding="utf-8")
        self.assertIn("stream()", text)
        self.assertNotIn("for (String value : input)", text)

        entries = [json.loads(l) for l in self.log_path.read_text(encoding="utf-8").splitlines()]
        kept = [e for e in entries if e.get("status") == "kept"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["technique"], "refactor_lambda_map")
        self.assertEqual(kept[0]["candidate_cc"], 0)

        log = subprocess.run(["git", "-C", str(self.work_path), "log", "-1",
                              "--pretty=%s"], capture_output=True, text=True, check=True)
        self.assertIn("refactor: lambda_map", log.stdout)

    def test_dry_run_leaves_workspace_untouched(self):
        status = rl.process_method("proj", self.work_path, self.row(), StubClient(),
                                   dry_run=True, stats=self.stats())
        self.assertEqual(status, "kept")
        text = (self.work_path / self.rel_file).read_text(encoding="utf-8")
        self.assertIn("for (String value : input)", text)

    def test_invalid_llm_output_is_rejected(self):
        client = StubClient(response="no es java")
        stats = self.stats()
        status = rl.process_method("proj", self.work_path, self.row(), client,
                                   dry_run=False, stats=stats)
        self.assertEqual(status, "no_valid_candidate")
        self.assertEqual(stats["kept_committed"], 0)
        self.assertIn("for (String value : input)",
                      (self.work_path / self.rel_file).read_text(encoding="utf-8"))

    def test_resume_skips_settled_methods(self):
        rl.process_method("proj", self.work_path, self.row(), StubClient(),
                          dry_run=False, stats=self.stats())
        done = rl.load_done_keys(self.log_path)
        self.assertIn(("proj", self.rel_file, self.start_line), done)


if __name__ == "__main__":
    unittest.main()
