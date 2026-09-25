"""Tests de la orquestación de refactor_loop.main (sin LLM ni git reales)."""

import argparse
import json
import unittest
from unittest import mock

import pandas as pd

import refactor_loop as rl
from tests.support import TempDirTestCase

TARGET = "refactor_lambda_map"


def args(**overrides):
    base = dict(project=None, limit=None, dry_run=False, fresh=False,
                log=None, resume=None, quiet=True)
    base.update(overrides)
    return argparse.Namespace(**base)


def dataset_rows(n_by_project):
    rows = []
    for project, count in n_by_project.items():
        for i in range(count):
            rows.append({"project": project, "file": "A.java",
                         "method_start_line": 10 + i,
                         "refactor_extract_method": 0,
                         "refactor_collapse_ifs_with_and": 0,
                         TARGET: 1,
                         "refactor_lambda_filter_map": 0,
                         "refactor_lambda_reduce": 0})
    return pd.DataFrame(rows)


def batch_entry_for(row):
    return {"project": row["project"], "rel_file": row["file"],
            "start_line": row["method_start_line"], "signature": "m()",
            "locate_sig": "C.m()", "techniques": [TARGET], "source": "x" * 30,
            "base": {}, "base_cc": 5, "base_cyclo": 2}


class MainOrchestrationTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.client = mock.Mock(api_key="k", base_url="http://x", model="m",
                                reasoning_effort=None)
        self.patches = [
            mock.patch.object(rl, "RUN_TESTS", "never"),
            mock.patch.object(rl, "REFACTOR_MODE", "stream"),
            mock.patch.object(rl, "WORK_DIR", str(self.tmp / "work")),
            mock.patch.object(rl.llm_client, "LLMClient", return_value=self.client),
            mock.patch.object(rl.progress_monitor, "maybe_launch_terminal",
                              return_value=None),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def dataset_file(self, frame):
        path = self.tmp / "classified.csv"
        frame.to_csv(path, index=False)
        return path

    def run_main(self, frame, cli_args, work_projects=("p1", "p2")):
        work_map = {name: self.tmp / "work" / name for name in work_projects}
        for path in work_map.values():
            path.mkdir(parents=True, exist_ok=True)
        dataset = self.dataset_file(frame)
        with mock.patch.object(rl, "parse_args", return_value=cli_args), \
             mock.patch.object(rl, "CLASSIFIED_DATASET", dataset), \
             mock.patch.object(rl.workspace, "ensure_workspace",
                               return_value=[(n, p) for n, p in work_map.items()]), \
             mock.patch.object(rl, "process_method", return_value="kept") as process, \
             mock.patch.object(rl, "process_batch", return_value=["kept"]) as batch:
            rl.main()
        return process, batch

    def test_stream_processes_pending_methods(self):
        frame = dataset_rows({"p1": 2})
        process, _ = self.run_main(frame, args(log=str(self.tmp / "log.jsonl")))
        self.assertEqual(process.call_count, 2)

    def test_projects_without_workspace_are_skipped(self):
        frame = dataset_rows({"p1": 1, "p9": 1})
        process, _ = self.run_main(frame, args(log=str(self.tmp / "log.jsonl")),
                                   work_projects=("p1",))
        self.assertEqual(process.call_count, 1)

    def test_limit_counts_resumed_rows(self):
        frame = dataset_rows({"p1": 3})
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps({"project": "p1", "file": "A.java",
                                   "start_line": 10, "status": "kept"}) + "\n",
                       encoding="utf-8")
        process, _ = self.run_main(frame, args(limit=2, log=str(log),
                                               resume=str(log)))
        # el primer método se reanuda; con --limit 2 solo se procesa uno nuevo
        self.assertEqual(process.call_count, 1)

    def test_project_filter(self):
        frame = dataset_rows({"p1": 1, "p2": 1})
        process, _ = self.run_main(frame, args(project="p2",
                                               log=str(self.tmp / "log.jsonl")))
        self.assertEqual(process.call_count, 1)
        row = process.call_args.args[2]
        self.assertEqual(row["project"], "p2")

    def test_fresh_ignores_previous_log(self):
        frame = dataset_rows({"p1": 1})
        log = self.tmp / "log.jsonl"
        log.write_text(json.dumps({"project": "p1", "file": "A.java",
                                   "start_line": 10, "status": "kept"}) + "\n",
                       encoding="utf-8")
        process, _ = self.run_main(frame, args(fresh=True, log=str(log)))
        self.assertEqual(process.call_count, 1)
        # el log previo se borra en --fresh; process_method está simulado y no reescribe
        self.assertFalse(log.exists())

    def test_missing_dataset_returns_early(self):
        with mock.patch.object(rl, "parse_args", return_value=args(
                log=str(self.tmp / "log.jsonl"))), \
             mock.patch.object(rl, "CLASSIFIED_DATASET", self.tmp / "nope.csv"), \
             mock.patch.object(rl.workspace, "ensure_workspace", return_value=[]), \
             mock.patch.object(rl, "process_method") as process:
            rl.main()
        process.assert_not_called()

    def test_batch_mode_chunks_by_max_methods(self):
        frame = dataset_rows({"p1": 5})
        work = self.tmp / "work" / "p1"
        work.mkdir(parents=True, exist_ok=True)
        chunks = []

        def fake_batch(client, work_path, entries, dry_run, stats):
            chunks.append(len(entries))
            return ["kept"] * len(entries)

        dataset = self.dataset_file(frame)
        with mock.patch.object(rl, "parse_args", return_value=args(
                log=str(self.tmp / "log.jsonl"))), \
             mock.patch.object(rl, "CLASSIFIED_DATASET", dataset), \
             mock.patch.object(rl, "REFACTOR_MODE", "batch"), \
             mock.patch.object(rl, "REFACTOR_BATCH_MAX_METHODS", 2), \
             mock.patch.object(rl, "REFACTOR_BATCH_OUTPUT_BUDGET", 10 ** 9), \
             mock.patch.object(rl, "batch_entry",
                               side_effect=lambda project, work_path, row:
                               (batch_entry_for(row), None)), \
             mock.patch.object(rl.workspace, "ensure_workspace",
                               return_value=[("p1", work)]), \
             mock.patch.object(rl, "process_batch", side_effect=fake_batch):
            rl.main()
        self.assertEqual(chunks, [2, 2, 1])

    def test_batch_mode_counts_missing_entries(self):
        frame = dataset_rows({"p1": 2})
        work = self.tmp / "work" / "p1"
        work.mkdir(parents=True, exist_ok=True)
        dataset = self.dataset_file(frame)
        with mock.patch.object(rl, "parse_args", return_value=args(
                log=str(self.tmp / "log.jsonl"))), \
             mock.patch.object(rl, "CLASSIFIED_DATASET", dataset), \
             mock.patch.object(rl, "REFACTOR_MODE", "batch"), \
             mock.patch.object(rl, "batch_entry", return_value=(None, "not_found")), \
             mock.patch.object(rl.workspace, "ensure_workspace",
                               return_value=[("p1", work)]), \
             mock.patch.object(rl, "process_batch") as batch:
            rl.main()
        batch.assert_not_called()
        entries = [json.loads(l) for l in
                   (self.tmp / "log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(1 for e in entries if e["status"] == "not_found"), 2)
        self.assertTrue(all(e.get("reason") for e in entries
                            if e["status"] == "not_found"))


if __name__ == "__main__":
    unittest.main()
