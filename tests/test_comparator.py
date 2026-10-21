import unittest
from backend.collector.models import ListResult, User
from backend.services.comparator import InsufficientData, non_followers


def complete(*users):
    return ListResult(list(users), len(users), "complete", "reference_verified",
                      1, True, True, True)


class ComparatorTests(unittest.TestCase):
    def test_difference_by_id_and_renamed_user(self):
        followers = complete(User("1", "old_name"), User("3", "fan"))
        following = complete(User("1", "new_name"), User("2", "other"))
        self.assertEqual(non_followers(followers, following), [User("2", "other")])

    def test_same_name_different_identity(self):
        self.assertEqual(non_followers(complete(User("1", "same")),
                                      complete(User("2", "same"))), [User("2", "same")])

    def test_verified_empty_lists(self):
        self.assertEqual(non_followers(complete(), complete()), [])

    def test_rejects_unverified_incomplete_or_inconsistent(self):
        for field, value in [("state", "incomplete"), ("state", "unavailable"),
                             ("integrity_verified", False), ("terminal_page", False),
                             ("counts_stable", False), ("reported_count", 99),
                             ("reported_count", None)]:
            for side in range(2):
                with self.subTest(field=field, value=value, side=side):
                    lists = [complete(), complete()]
                    setattr(lists[side], field, value)
                    with self.assertRaises(InsufficientData):
                        non_followers(*lists)

    def test_rejects_duplicates_and_missing_id(self):
        for users in [(User("1", "a"), User("1", "b")), (User("", "a"),)]:
            with self.assertRaises(InsufficientData):
                non_followers(complete(*users), complete())
