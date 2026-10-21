import copy
import unittest

from backend.services.coverage import list_coverage, report_coverage


def observed(ids, reported=100):
    return {"state": "incomplete", "reported_count": reported,
            "reported_count_after": reported,
            "structured_rows": [{"id": str(i), "username": f"person_{i}"} for i in ids],
            "ui_rows": [{"username": f"person_{i}"} for i in ids]}


class CoverageTests(unittest.TestCase):
    def test_98_percent_coverage_does_not_imply_98_percent_precision(self):
        # Both true lists could contain IDs 1..100. Observed following\followers
        # would label 99 and 100 incorrectly, despite 98% coverage of both lists.
        report = {"followers": observed(range(1, 99)), "following": observed(range(3, 101))}
        result = report_coverage(report)
        self.assertTrue(result["both_lists_target_met"])
        self.assertIsNone(result["precision_percent"])
        self.assertFalse(result["definitive_comparison_allowed"])

    def test_each_list_must_meet_target_without_averaging(self):
        result = report_coverage({"followers": observed([], 337),
                                  "following": observed(range(1, 511), 511)})
        self.assertFalse(result["both_lists_target_met"])
        self.assertEqual(result["minimum_list_coverage_percent"], 0)
        self.assertEqual(result["lists"]["following"]["observed_coverage_percent"], 99.804)

    def test_round_up_threshold_and_no_false_positive_from_display_rounding(self):
        item = observed(range(1, 331), 337)
        self.assertEqual(list_coverage(item)["required_ids_for_target"], 331)
        self.assertFalse(list_coverage(item)["target_met"])
        self.assertTrue(list_coverage(observed(range(1, 332), 337))["target_met"])

    def test_counter_change_unknown_and_response_loss_prevent_target(self):
        base = observed(range(1, 101))
        for changes in ({"reported_count_after": 101}, {"reported_count": None},
                        {"parse_limit_events": 1}, {"inflight_requests_at_end": 1}):
            with self.subTest(changes=changes):
                self.assertFalse(list_coverage({**base, **changes})["target_met"])

    def test_conflicts_and_mismatched_names_prevent_target(self):
        item = observed(range(1, 101))
        duplicate = copy.deepcopy(item)
        duplicate["structured_rows"][-1]["id"] = "1"
        self.assertFalse(list_coverage(duplicate)["target_met"])
        item["structured_rows"][-1]["username"] = "not_in_ui"
        self.assertFalse(list_coverage(item)["target_met"])

    def test_unknown_and_unavailable_empty_lists_are_not_success(self):
        self.assertIsNone(list_coverage(None)["observed_coverage_percent"])
        self.assertFalse(list_coverage({"state": "unavailable", "reported_count": 0,
                                      "reported_count_after": 0})["target_met"])


if __name__ == "__main__":
    unittest.main()
