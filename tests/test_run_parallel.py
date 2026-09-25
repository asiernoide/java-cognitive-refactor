"""Tests del lanzador paralelo."""

import json
import unittest
from unittest import mock

import run_parallel as rp
from tests.support import TempDirTestCase


class ResolveDirTests(TempDirTestCase):
    def test_relative_resolves_against_repo_root(self):
        with mock.patch.dict("os.environ", {"DATA_DIR": "datos"}, clear=False):
            self.assertEqual(rp.resolve_dir("DATA_DIR", "data"), rp.ROOT / "datos")

    def test_absolute_is_kept(self):
        with mock.patch.dict("os.environ", {"DATA_DIR": str(self.tmp)}, clear=False):
            self.assertEqual(rp.resolve_dir("DATA_DIR", "data"), self.tmp)

    def test_default_used_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(rp.resolve_dir("DATA_DIR", "data"), rp.ROOT / "data")


class ProjectGroupsTests(TempDirTestCase):
    def test_round_robin_distribution(self):
        projects = self.tmp / "projects"
        for name in ["a", "b", "c", "d", "e"]:
            (projects / name).mkdir(parents=True)
        with mock.patch.object(rp, "PROJECTS", projects):
            groups = rp.project_groups(2)
        self.assertEqual(groups, [["a", "c", "e"], ["b", "d"]])

    def test_more_workers_than_projects(self):
        projects = self.tmp / "projects"
        (projects / "a").mkdir(parents=True)
        with mock.patch.object(rp, "PROJECTS", projects):
            groups = rp.project_groups(4)
        self.assertEqual(groups, [["a"]])


class MergeLogsTests(TempDirTestCase):
    def test_merges_sorts_and_removes_worker_logs(self):
        data = self.tmp / "data"
        data.mkdir()
        main_log = data / "refactor_log.jsonl"
        main_log.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00", "n": 0}) + "\n",
                            encoding="utf-8")
        (data / "refactor_log_w0.jsonl").write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:03", "n": 3}) + "\n" +
            json.dumps({"timestamp": "2026-01-01T00:00:01", "n": 1}) + "\n",
            encoding="utf-8")
        (data / "refactor_log_w1.jsonl").write_text(
            "linea corrupta\n" +
            json.dumps({"timestamp": "2026-01-01T00:00:02", "n": 2}) + "\n",
            encoding="utf-8")
        with mock.patch.object(rp, "DATA", data), \
             mock.patch.object(rp, "MAIN_LOG", main_log):
            count = rp.merge_worker_logs()
        self.assertEqual(count, 3)
        entries = [json.loads(l) for l in main_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["n"] for e in entries], [0, 1, 2, 3])
        self.assertEqual(sorted(p.name for p in data.glob("refactor_log_w*.jsonl")), [])

    def test_no_worker_logs(self):
        data = self.tmp / "data"
        data.mkdir()
        with mock.patch.object(rp, "DATA", data), \
             mock.patch.object(rp, "MAIN_LOG", data / "refactor_log.jsonl"):
            self.assertEqual(rp.merge_worker_logs(), 0)


if __name__ == "__main__":
    unittest.main()
