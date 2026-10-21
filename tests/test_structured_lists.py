import unittest

from backend.experimental.structured_lists import InvalidPage, assess_pages, parse_page


def gql(direction, users, cursor=None):
    return {"data": {f"xdt_api__v1__friendships__{direction}": {
        "users": users, "next_max_id": cursor}}, "status": "ok"}


def rest(users, cursor=None):
    return {"users": users, "next_max_id": cursor, "status": "ok"}


class StructuredListsTests(unittest.TestCase):
    def test_private_graphql_cursor_and_stable_ids(self):
        pages = [gql("followers", [{"pk_id": "10", "username": "Alice"}], "next"),
                 gql("followers", [{"pk": 10, "username": "alice"},
                                   {"pk": 20, "username": "Bob"}])]
        result = assess_pages(pages, "private_graphql", "followers", 2)
        self.assertEqual([(row.id, row.username) for row in result.rows],
                         [("10", "alice"), ("20", "bob")])
        self.assertEqual(result.duplicate_ids, 1)
        self.assertEqual(result.state, "terminal_unverified")
        self.assertFalse(result.integrity_verified)

    def test_rest_terminal_count_mismatch_is_incomplete(self):
        result = assess_pages([rest([{"pk": 10, "username": "alice"}])],
                              "private_rest_v1", "following", 2)
        self.assertTrue(result.terminal_observed)
        self.assertFalse(result.count_matches)
        self.assertEqual(result.reason, "terminal_count_mismatch")
        self.assertEqual(result.state, "incomplete")

    def test_cursor_left_over_is_incomplete(self):
        result = assess_pages([rest([{"pk": 10, "username": "alice"}], "next")],
                              "private_rest_v1", "followers", 1)
        self.assertFalse(result.terminal_observed)
        self.assertEqual(result.reason, "pagination_not_finished")

    def test_repeated_cursor_is_not_treated_as_complete(self):
        pages = [rest([{"pk": 10, "username": "alice"}], "same"),
                 rest([{"pk": 10, "username": "alice"}], "same")]
        result = assess_pages(pages, "private_rest_v1", "followers", 1)
        self.assertEqual(result.reason, "repeated_cursor")
        self.assertEqual(result.state, "incomplete")

    def test_stopped_early_overrides_terminal(self):
        result = assess_pages([rest([{"pk": 10, "username": "alice"}])],
                              "private_rest_v1", "followers", 1, stopped_early=True)
        self.assertEqual(result.state, "incomplete")
        self.assertFalse(result.terminal_observed)

    def test_missing_id_is_invalid(self):
        with self.assertRaises(InvalidPage):
            parse_page(rest([{"username": "alice"}]), "private_rest_v1", "followers")

    def test_conflicting_identity_is_invalid(self):
        pages = [gql("following", [{"pk": 10, "username": "alice"}], "n"),
                 gql("following", [{"pk": 10, "username": "bob"}])]
        self.assertEqual(assess_pages(pages, "private_graphql", "following").state,
                         "invalid")

    def test_graphql_root_and_terminal_order(self):
        with self.assertRaises(InvalidPage):
            parse_page(gql("followers", []), "private_graphql", "following")
        pages = [rest([]), rest([])]
        self.assertEqual(assess_pages(pages, "private_rest_v1", "followers").reason,
                         "pages_after_terminal")


if __name__ == "__main__":
    unittest.main()
