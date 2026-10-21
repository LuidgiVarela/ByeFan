import copy
import unittest

from backend.collector.models import Collection, ListResult, User
from backend.services.validation import validate_reference


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.first = Collection("fixture", "2026-10-18T00:00:00Z", "10",
            ListResult([User("1", "a")], 1, "incomplete", "integrity_unverified", 1, True, True),
            ListResult([User("2", "b")], 1, "incomplete", "integrity_unverified", 1, True, True))
        self.second = copy.deepcopy(self.first)
        self.reference = {"username": "fixture", "profile_id": "10", "followers_ids": ["1"], "following_ids": ["2"]}

    def test_promote_only_with_matching_reference_and_repeat(self):
        self.assertTrue(validate_reference(self.first, self.second, self.reference))
        self.assertEqual(self.first.followers.state, "complete")

    def test_equal_counts_different_ids_cannot_promote(self):
        self.second.followers.users = [User("3", "c")]
        self.assertFalse(validate_reference(self.first, self.second, self.reference))
        self.assertFalse(self.first.followers.integrity_verified)

    def test_wrong_or_missing_reference_and_partial_run(self):
        for reference in ({}, dict(self.reference, followers_ids=["3"]),
                          dict(self.reference, following_ids=["2", "2"])):
            self.assertFalse(validate_reference(self.first, self.second, reference))
        self.second.error = "rate_limited"
        self.assertFalse(validate_reference(self.first, self.second, self.reference))
