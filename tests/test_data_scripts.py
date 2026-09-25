"""Tests de los scripts de datos (agregación y dataset de entrenamiento)."""

import json
import unittest
from unittest import mock

import pandas as pd

import data.aggregate_method_data as aggregate
import data.build_refactor_dataset as builder
from tests.support import TempDirTestCase


class AggregateTests(TempDirTestCase):
    def _write_project_csv(self, name, rows):
        pd.DataFrame(rows).to_csv(self.tmp / f"final_methods_dataset_{name}.csv",
                                  index=False)

    def test_aggregates_sorted_with_project_column(self):
        self._write_project_csv("b", [{"file": "B.java", "loc": 2}])
        self._write_project_csv("a", [{"file": "A.java", "loc": 1}])
        with mock.patch.object(aggregate, "DATA_DIR", self.tmp):
            aggregate.main()
        out = pd.read_csv(self.tmp / "aggregated_method_data.csv")
        self.assertEqual(list(out["project"]), ["a", "b"])
        self.assertEqual(list(out["loc"]), [1, 2])

    def test_no_input_files_writes_nothing(self):
        with mock.patch.object(aggregate, "DATA_DIR", self.tmp):
            aggregate.main()
        self.assertFalse((self.tmp / "aggregated_method_data.csv").exists())

    def test_runs_from_any_cwd(self):
        self._write_project_csv("p", [{"file": "P.java"}])
        # DATA_DIR se resuelve desde __file__, no desde el CWD
        with mock.patch.object(aggregate, "DATA_DIR", self.tmp):
            aggregate.main()
        self.assertTrue((self.tmp / "aggregated_method_data.csv").exists())


class BuildDatasetTests(TempDirTestCase):
    def _log(self, entries):
        log = self.tmp / "log.jsonl"
        log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
        return log

    def test_last_kept_wins_even_if_later_keep_original(self):
        log = self._log([
            {"project": "p", "file": "A.java", "start_line": 10, "status": "kept",
             "technique": "refactor_lambda_map"},
            {"project": "p", "file": "A.java", "start_line": 10, "status": "keep_original"},
            {"project": "p", "file": "A.java", "start_line": 11, "status": "llm_error"},
        ])
        chosen = builder.load_chosen_technique(log)
        self.assertEqual(chosen, {("p", "A.java", 10): "refactor_lambda_map"})

    def test_ignores_unknown_techniques(self):
        log = self._log([
            {"project": "p", "file": "A.java", "start_line": 10, "status": "kept",
             "technique": "otra"},
        ])
        self.assertEqual(builder.load_chosen_technique(log), {})

    def test_project_csv_marks_single_technique(self):
        csv = self.tmp / "final_methods_dataset_p.csv"
        pd.DataFrame([
            {"file": "A.java", "method_start_line": 10, "loc": 1},
            {"file": "A.java", "method_start_line": 20, "loc": 2},
            {"file": "A.java", "method_start_line": 20, "loc": 2},  # duplicado
        ]).to_csv(csv, index=False)
        chosen = {("p", "A.java", 10): "refactor_lambda_map"}
        df = builder.build_project_csv(csv, "p", chosen)
        self.assertEqual(len(df), 2)
        self.assertEqual(df["project"].tolist(), ["p", "p"])
        self.assertEqual(int(df["refactor_lambda_map"].sum()), 1)
        self.assertLessEqual(int(df[builder.TARGETS].sum(axis=1).max()), 1)

    def test_project_csv_all_zero_without_kept(self):
        csv = self.tmp / "final_methods_dataset_p.csv"
        pd.DataFrame([{"file": "A.java", "method_start_line": 10}]).to_csv(csv, index=False)
        df = builder.build_project_csv(csv, "p", {})
        self.assertEqual(int(df[builder.TARGETS].sum(axis=1).sum()), 0)


if __name__ == "__main__":
    unittest.main()
