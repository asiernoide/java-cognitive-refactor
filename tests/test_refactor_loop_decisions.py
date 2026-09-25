"""Tests de las decisiones del bucle: finalize_method, process_batch y parseo de lotes."""

import random
import unittest
from pathlib import Path
from unittest import mock

import refactor_loop as rl
from tests.support import TempDirTestCase


def candidate(technique="refactor_lambda_map", cc=3, cyclo=1, **extra):
    cand = {"technique": technique, "cc": cc, "cyclo": cyclo, "loc": 5,
            "invocations": 2, "tokens": 7, "code": "code-" + technique,
            "new_methods": [], "main_cc": cc, "extracted_cc": 0,
            "main_cyclo": cyclo, "extracted_cyclo": 0}
    cand.update(extra)
    return cand


def fresh_stats():
    return {k: 0 for k in ["skipped_no_file", "skipped_no_technique", "skipped_not_found",
                           "candidates", "no_valid_candidate", "kept_original",
                           "kept_committed", "kept_dry_run", "apply_failed",
                           "methods_refactored", "llm_errors", "batch_retries"]}


class FinalizeMethodTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.applied = []
        self.git_calls = []
        self.patches = [
            mock.patch.object(rl, "log_entry", side_effect=lambda **kw: self.logs.append(kw)),
            mock.patch.object(rl, "apply_candidate",
                              side_effect=lambda path, base, code, nms:
                              (self.applied.append((code, nms)) or True)),
            mock.patch.object(rl.ast, "analyze_method_by_signature",
                              side_effect=lambda path, sig: {"method_signature": sig}),
            mock.patch.object(rl.workspace, "git_restore",
                              side_effect=lambda *a, **k: self.git_calls.append("restore")),
            mock.patch.object(rl.workspace, "git_commit",
                              side_effect=lambda *a, **k: self.git_calls.append("commit")),
            mock.patch.object(rl, "RNG", random.Random(42)),
            mock.patch.object(rl, "MAX_CYCLO_DELTA_PCT", 0.0),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def finalize(self, candidates, base_cc=5, base_cyclo=2, dry_run=False):
        stats = fresh_stats()
        status = rl.finalize_method("proj", self.tmp, "A.java", 10, "m()", "C.m()",
                                    base_cc, base_cyclo, candidates, dry_run, stats)
        return status, stats

    def test_no_candidates(self):
        status, stats = self.finalize([])
        self.assertEqual(status, "no_valid_candidate")
        self.assertEqual(stats["no_valid_candidate"], 1)
        self.assertEqual(self.logs[-1]["status"], "no_valid_candidate")
        self.assertEqual(self.applied, [])

    def test_kept_commits_and_logs(self):
        status, stats = self.finalize([candidate()])
        self.assertEqual(status, "kept")
        self.assertEqual(stats["kept_committed"], 1)
        self.assertEqual(stats["methods_refactored"], 1)
        self.assertEqual(self.git_calls, ["commit"])
        self.assertEqual(self.applied[0][0], "code-refactor_lambda_map")
        kept = [e for e in self.logs if e.get("status") == "kept"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["decision"], "kept+commit")

    def test_dry_run_restores_without_commit(self):
        status, stats = self.finalize([candidate()], dry_run=True)
        self.assertEqual(status, "kept")
        self.assertEqual(stats["kept_dry_run"], 1)
        self.assertEqual(self.git_calls, ["restore"])
        self.assertEqual(self.logs[-1]["decision"], "kept(dry-run)")

    def test_negative_score_keeps_original(self):
        status, stats = self.finalize([candidate(cc=6, cyclo=9)], base_cc=5, base_cyclo=2)
        self.assertEqual(status, "keep_original")
        self.assertEqual(stats["kept_original"], 1)
        self.assertEqual(self.applied, [])

    def test_cyclo_cap_filters_candidates(self):
        with mock.patch.object(rl, "MAX_CYCLO_DELTA_PCT", 50.0):
            status, _ = self.finalize([candidate(cc=3, cyclo=5)],
                                      base_cc=5, base_cyclo=2)
        self.assertEqual(status, "keep_original")  # 5 vs 2 = +150% > 50%
        self.assertTrue(any("todos los candidatos superan" in e.get("reason", "")
                            for e in self.logs))

    def test_cyclo_cap_keeps_allowed_subset(self):
        good = candidate("refactor_lambda_map", cc=3, cyclo=2)
        bad = candidate("refactor_lambda_reduce", cc=3, cyclo=10)
        with mock.patch.object(rl, "MAX_CYCLO_DELTA_PCT", 50.0):
            status, _ = self.finalize([good, bad], base_cc=5, base_cyclo=2)
        self.assertEqual(status, "kept")
        self.assertEqual(self.applied[0][0], "code-refactor_lambda_map")
        self.assertTrue(any("descartados candidatos" in e.get("reason", "")
                            for e in self.logs))

    def test_apply_failure(self):
        with mock.patch.object(rl, "apply_candidate", return_value=False):
            status, stats = self.finalize([candidate()])
        self.assertEqual(status, "apply_failed")
        self.assertEqual(stats["apply_failed"], 1)
        self.assertEqual(self.logs[-1]["status"], "apply_failed")

    def test_relocate_before_apply_can_fail(self):
        with mock.patch.object(rl.ast, "analyze_method_by_signature", return_value=None):
            status, stats = self.finalize([candidate()])
        self.assertEqual(status, "apply_failed")
        self.assertEqual(stats["apply_failed"], 1)

    def test_candidate_scores_logged_with_single_kept(self):
        self.finalize([candidate("a", cc=4, cyclo=2), candidate("b", cc=3, cyclo=1)])
        valid = [e for e in self.logs if e.get("valid") is True]
        self.assertEqual(len(valid), 2)
        self.assertEqual(sum(1 for e in valid if e.get("kept")), 1)
        self.assertEqual([e["technique"] for e in valid], ["a", "b"])


class ProcessBatchAlignmentTests(TempDirTestCase):
    def _entries(self):
        return [{"project": "p", "rel_file": "A.java", "start_line": i,
                 "signature": f"m{i}()", "locate_sig": f"m{i}()", "techniques": ["t"],
                 "source": "src", "base": {}, "base_cc": 5, "base_cyclo": 2}
                for i in range(3)]

    def test_statuses_aligned_with_entries(self):
        entries = self._entries()
        calls = {"finalize": [], "fallback": []}

        def eval_batch(work_path, entry, refactors, tokens, stats):
            if entry["start_line"] == 1:
                return [], "fallo"
            return [candidate()], ""

        def retry(client, work_path, failed, tokens, stats, retries_left):
            return [], list(failed)

        def fallback(client, work_path, entry, dry_run, stats, reason):
            calls["fallback"].append(entry["start_line"])
            return "llm_error"

        def finalize(project, work_path, rel_file, start_line, sig, lsig, bcc, bcy,
                     cands, dry_run, stats):
            calls["finalize"].append(start_line)
            return "kept"

        with mock.patch.object(rl, "_batch_call", return_value=({"refactors": []}, {})), \
             mock.patch.object(rl, "_evaluate_batch_method", side_effect=eval_batch), \
             mock.patch.object(rl, "_retry_batch", side_effect=retry), \
             mock.patch.object(rl, "_fallback_or_error", side_effect=fallback), \
             mock.patch.object(rl, "finalize_method", side_effect=finalize), \
             mock.patch.object(rl, "REFACTOR_BATCH_RETRIES", 0):
            statuses = rl.process_batch(object(), self.tmp, entries, False, fresh_stats())

        self.assertEqual(statuses, ["kept", "llm_error", "kept"])
        self.assertEqual(calls["fallback"], [1])
        self.assertEqual(calls["finalize"], [0, 2])

    def test_total_batch_failure_splits(self):
        entries = self._entries()[:2]
        fallback_calls = []

        def fallback(client, work_path, entry, dry_run, stats, reason):
            fallback_calls.append(entry["start_line"])
            return "llm_error"

        with mock.patch.object(rl, "_batch_call", return_value=(None, None)), \
             mock.patch.object(rl, "_fallback_or_error", side_effect=fallback):
            statuses = rl.process_batch(object(), self.tmp, entries, False, fresh_stats())
        self.assertEqual(statuses, ["llm_error", "llm_error"])
        self.assertEqual(fallback_calls, [0, 1])

    def test_single_entry_failure_uses_fallback(self):
        entries = self._entries()[:1]
        with mock.patch.object(rl, "_batch_call", return_value=(None, None)), \
             mock.patch.object(rl, "_fallback_or_error", return_value="llm_error"):
            statuses = rl.process_batch(object(), self.tmp, entries, False, fresh_stats())
        self.assertEqual(statuses, ["llm_error"])

    def test_retry_preserves_original_index(self):
        entry = self._entries()[0]
        entry["start_line"] = 7
        cand = [candidate()]

        with mock.patch.object(rl, "_batch_call", return_value=({"refactors": []}, {})), \
             mock.patch.object(rl, "_evaluate_batch_method",
                               side_effect=lambda *a, **k: (cand, "")):
            results, failed = rl._retry_batch(object(), self.tmp,
                                              [(7, entry, "err")], 10, fresh_stats(), 1)
        self.assertEqual(results, [(7, entry, cand)])
        self.assertEqual(failed, [])


class StreamCandidateTests(unittest.TestCase):
    def test_generates_one_candidate_per_success(self):
        stats = fresh_stats()
        calls = []

        def try_technique(client, project, work_path, rel_file, base, tech, locate_sig):
            calls.append(tech)
            return candidate(tech) if tech != "refactor_lambda_reduce" else None

        with mock.patch.object(rl, "try_technique", side_effect=try_technique):
            cands = rl.generate_stream_candidates(object(), "p", Path("."), "A.java",
                                                  {}, ["refactor_lambda_map",
                                                       "refactor_lambda_reduce"],
                                                  "C.m()", stats)
        self.assertEqual([c["technique"] for c in cands], ["refactor_lambda_map"])
        self.assertEqual(stats["candidates"], 2)
        self.assertEqual(calls, ["refactor_lambda_map", "refactor_lambda_reduce"])


class ProcessMethodTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.patches = [
            mock.patch.object(rl, "log_entry", side_effect=lambda **kw: self.logs.append(kw)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.work = self.tmp / "work"
        self.work.mkdir()
        (self.work / "A.java").write_text("class T {}\n", encoding="utf-8")

    def row(self, **overrides):
        base = {"file": "A.java", "method_start_line": 10,
                "refactor_lambda_map": 1, "refactor_extract_method": 0,
                "refactor_collapse_ifs_with_and": 0, "refactor_lambda_filter_map": 0,
                "refactor_lambda_reduce": 0}
        base.update(overrides)
        return base

    def test_no_file(self):
        stats = fresh_stats()
        status = rl.process_method("p", self.work, self.row(file="Nope.java"),
                                   object(), False, stats)
        self.assertEqual(status, "no_file")
        self.assertEqual(stats["skipped_no_file"], 1)

    def test_no_technique(self):
        stats = fresh_stats()
        status = rl.process_method("p", self.work, self.row(refactor_lambda_map=0),
                                   object(), False, stats)
        self.assertEqual(status, "no_technique")
        self.assertEqual(stats["skipped_no_technique"], 1)

    def test_not_found_in_original(self):
        stats = fresh_stats()
        with mock.patch.object(rl, "locate_original", return_value=None):
            status = rl.process_method("p", self.work, self.row(), object(), False, stats)
        self.assertEqual(status, "not_found")
        self.assertEqual(stats["skipped_not_found"], 1)
        self.assertEqual(self.logs[-1]["status"], "not_found")

    def test_not_found_in_copy(self):
        stats = fresh_stats()
        with mock.patch.object(rl, "locate_original",
                               return_value=(Path("orig"), "C.m()", {"method_signature": "m()"})), \
             mock.patch.object(rl.ast, "analyze_method_by_signature", return_value=None):
            status = rl.process_method("p", self.work, self.row(), object(), False, stats)
        self.assertEqual(status, "not_found")
        self.assertEqual(self.logs[-1]["reason"],
                         "método no localizado en la copia (firma cualificada)")

    def test_success_delegates_to_finalize(self):
        base = {"method_signature": "m()", "cognitive_complexity": 5,
                "cyclomatic_complexity": 2}
        captured = {}

        def finalize(project, work_path, rel_file, start_line, signature, locate_sig,
                     base_cc, base_cyclo, candidates, dry_run, stats):
            captured.update(signature=signature, locate_sig=locate_sig,
                            base_cc=base_cc, base_cyclo=base_cyclo,
                            candidates=candidates)
            return "kept"

        with mock.patch.object(rl, "locate_original",
                               return_value=(Path("orig"), "C.m()", {"method_signature": "m()"})), \
             mock.patch.object(rl.ast, "analyze_method_by_signature", return_value=base), \
             mock.patch.object(rl, "generate_stream_candidates", return_value=[candidate()]), \
             mock.patch.object(rl, "finalize_method", side_effect=finalize):
            status = rl.process_method("p", self.work, self.row(), object(), False,
                                       fresh_stats())
        self.assertEqual(status, "kept")
        self.assertEqual(captured["signature"], "m()")
        self.assertEqual(captured["locate_sig"], "C.m()")
        self.assertEqual(captured["base_cc"], 5)
        self.assertEqual(captured["base_cyclo"], 2)
        self.assertEqual(len(captured["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
