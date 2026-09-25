"""Tests del panel de progreso (solo utilidades, sin abrir consolas)."""

import os
import unittest
from unittest import mock

from lib import progress_monitor as pm
from tests.support import TempDirTestCase


class ReadCountsTests(TempDirTestCase):
    def test_counts_lines_and_kept(self):
        path = self.tmp / "log.jsonl"
        path.write_text(
            '{"status": "kept"}\n'
            '{"status": "llm_error"}\n'
            '{"status": "kept"}\n',
            encoding="utf-8")
        lines, kept = pm._read_counts(path)
        self.assertEqual(lines, 3)
        self.assertEqual(kept, 2)

    def test_missing_file(self):
        self.assertEqual(pm._read_counts(self.tmp / "nope.jsonl"), (0, 0))


class TailTests(TempDirTestCase):
    def test_returns_last_lines(self):
        path = self.tmp / "log.jsonl"
        path.write_text("\n".join(f"l{i}" for i in range(10)), encoding="utf-8")
        self.assertEqual(pm._tail(path, 3), ["l7", "l8", "l9"])

    def test_missing_file(self):
        self.assertEqual(pm._tail(self.tmp / "nope"), [])


class PidAliveTests(unittest.TestCase):
    def test_current_process_alive(self):
        self.assertTrue(pm._pid_alive(os.getpid()))

    def test_invalid_pid(self):
        self.assertFalse(pm._pid_alive(0))
        self.assertFalse(pm._pid_alive(-5))


class LaunchTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        with mock.patch.dict("os.environ", {"SHOW_TERMINAL": "0"}, clear=False):
            self.assertIsNone(pm.maybe_launch_terminal("data", "data/x.jsonl"))

    def test_unset_returns_none(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(pm.maybe_launch_terminal("data", "data/x.jsonl"))

    def test_truthy_values(self):
        self.assertEqual(pm.TRUTHY, {"1", "true", "yes", "on"})


if __name__ == "__main__":
    unittest.main()
