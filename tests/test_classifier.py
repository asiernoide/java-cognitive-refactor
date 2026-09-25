"""Tests del clasificador determinista de técnicas de refactorización."""

import unittest

import pandas as pd

import metrics_classifier as mc

TARGETS = mc.TARGETS


def row(**overrides):
    base = {
        "nested_if_chains": 0,
        "map_candidate_loops": 0,
        "filter_map_candidate_loops": 0,
        "reduce_candidate_loops": 0,
        "loc": 0,
        "statement_count": 0,
        "branch_count": 0,
        "max_if_nesting": 0,
        "nested_if_chains_with_else": 0,
        "foreach_count": 0,
    }
    base.update(overrides)
    return pd.Series(base)


class RuleTests(unittest.TestCase):
    def test_collapse_rule(self):
        labels = mc.classify_from_metrics(row(nested_if_chains=2))
        self.assertEqual(labels["refactor_collapse_ifs_with_and"], 1)
        self.assertEqual(sum(labels.values()), 1)

    def test_map_rule(self):
        labels = mc.classify_from_metrics(row(map_candidate_loops=1))
        self.assertEqual(labels["refactor_lambda_map"], 1)

    def test_filter_map_rule(self):
        labels = mc.classify_from_metrics(row(filter_map_candidate_loops=3))
        self.assertEqual(labels["refactor_lambda_filter_map"], 1)

    def test_reduce_rule(self):
        labels = mc.classify_from_metrics(row(reduce_candidate_loops=1))
        self.assertEqual(labels["refactor_lambda_reduce"], 1)

    def test_no_rules_no_labels(self):
        labels = mc.classify_from_metrics(row())
        self.assertEqual(sum(labels.values()), 0)

    def test_never_more_than_two_labels(self):
        labels = mc.classify_from_metrics(row(nested_if_chains=1, map_candidate_loops=1,
                                              filter_map_candidate_loops=1,
                                              reduce_candidate_loops=1))
        self.assertLessEqual(sum(labels.values()), 2)


class ExtractHeuristicTests(unittest.TestCase):
    def test_score_conditions(self):
        self.assertEqual(mc.extract_method_score(row()), 0)
        self.assertEqual(mc.extract_method_score(row(loc=80, statement_count=40)), 1)
        self.assertEqual(mc.extract_method_score(row(branch_count=8)), 1)
        self.assertEqual(mc.extract_method_score(row(max_if_nesting=3)), 1)
        self.assertEqual(mc.extract_method_score(row(nested_if_chains_with_else=2)), 1)
        self.assertEqual(mc.extract_method_score(row(foreach_count=2, statement_count=20)), 1)
        self.assertEqual(
            mc.extract_method_score(row(loc=80, statement_count=40, branch_count=8)),
            2)

    def test_thresholds_are_strict(self):
        self.assertEqual(mc.extract_method_score(row(loc=79, statement_count=40)), 0)
        self.assertEqual(mc.extract_method_score(row(loc=80, statement_count=39)), 0)
        self.assertEqual(mc.extract_method_score(row(branch_count=7)), 0)
        self.assertEqual(mc.extract_method_score(row(max_if_nesting=2)), 0)
        self.assertEqual(mc.extract_method_score(row(nested_if_chains_with_else=1)), 0)
        self.assertEqual(mc.extract_method_score(row(foreach_count=2, statement_count=19)), 0)

    def test_activated_with_two_conditions(self):
        labels = mc.classify_from_metrics(row(loc=80, statement_count=40, branch_count=8))
        self.assertEqual(labels["refactor_extract_method"], 1)

    def test_not_activated_with_one_condition(self):
        labels = mc.classify_from_metrics(row(loc=80, statement_count=40))
        self.assertEqual(labels["refactor_extract_method"], 0)


class PrioritizationTests(unittest.TestCase):
    def test_direct_patterns_beat_extract(self):
        labels = mc.classify_from_metrics(row(
            nested_if_chains=1, map_candidate_loops=1,
            loc=80, statement_count=40, branch_count=8))
        self.assertEqual(labels["refactor_collapse_ifs_with_and"], 1)
        self.assertEqual(labels["refactor_lambda_map"], 1)
        self.assertEqual(labels["refactor_extract_method"], 0)

    def test_tie_break_by_activation_metric(self):
        labels = mc.classify_from_metrics(row(map_candidate_loops=2,
                                              filter_map_candidate_loops=3,
                                              nested_if_chains=1))
        self.assertEqual(labels["refactor_lambda_filter_map"], 1)
        self.assertEqual(labels["refactor_lambda_map"], 1)
        self.assertEqual(labels["refactor_collapse_ifs_with_and"], 0)

    def test_top_two_only(self):
        labels = mc.classify_from_metrics(row(
            map_candidate_loops=5, filter_map_candidate_loops=1,
            nested_if_chains=1, reduce_candidate_loops=1))
        self.assertEqual(labels["refactor_lambda_map"], 1)
        selected = [t for t in TARGETS if labels[t] == 1]
        self.assertEqual(len(selected), 2)


if __name__ == "__main__":
    unittest.main()
