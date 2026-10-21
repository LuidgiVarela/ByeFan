"""Fictional rows and mocked HTTP only. No Actor is started by these tests."""
import contextlib
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from backend.experimental.apify import (
    API, Budget, ExperimentError, Plan, TARGET, compare_reference, effective_pricing, execute, normalize)
from scripts.apify_probe import main


def metadata(minimum=0.002):
    return {"data": {"pricingInfos": [{"startedAt": "2026-08-01T00:00:00Z",
              "pricingModel": "PAY_PER_EVENT", "minimalMaxTotalChargeUsd": minimum}]}}


def run(status="SUCCEEDED", cost=0.01, cap=0.05):
    return {"data": {"id": "run123", "status": status, "defaultDatasetId": "data123",
            "options": {"maxTotalChargeUsd": cap}, "usageTotalUsd": cost}}


class FakeAPI:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, *args):
        self.calls.append(args)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def row(provider="apify", uid="1", name="fictional_one", direction="followers"):
    common = {"userId": uid, "username": name}
    if provider == "apify":
        return dict(common, sourceUsername=TARGET, type="FOLLOWER" if direction == "followers" else "FOLLOWING")
    return dict(common, seedUsername=TARGET, direction=direction, status="delivered",
                **{"followerOf" if direction == "followers" else "followingOf": TARGET})


class ApifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.budget = Budget(Path(self.tmp.name) / "budget.sqlite3")
        self.budget.initialize("0.10")
        self.plan = Plan("apify", "followers", "0.05")

    def test_authorization_gate_before_budget_or_network(self):
        api = FakeAPI()
        with self.assertRaisesRegex(ExperimentError, "not_authorized"):
            execute(self.plan, api, self.budget)
        self.assertEqual(api.calls, [])
        self.budget.reserve("0.10")

    def test_offline_cli_plan_never_constructs_api(self):
        with patch("sys.argv", ["probe", "probe", "apify", "followers", "--cap-usd", "0.05"]), \
                patch("scripts.apify_probe.API", side_effect=AssertionError("network")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue())["network_requests"], 0)

    def test_target_and_bounds(self):
        for kwargs in ({"username": "someone_else"}, {"limit": 11}, {"limit": 0},
                       {"cap_usd": "NaN"}, {"cap_usd": "0"}, {"cap_usd": "-1"},
                       {"cap_usd": "Infinity"}, {"cap_usd": "0.0000001"}):
            args = dict(provider="apify", direction="followers", cap_usd="0.05") | kwargs
            with self.assertRaises(ExperimentError):
                Plan(**args)

    def test_provider_contracts(self):
        a = self.plan.preview()
        s = Plan("steadyfetch", "following", "0.05").preview()
        self.assertEqual(a["input"], {"usernames": [TARGET], "resultsLimit": 5, "dataToScrape": "followers"})
        self.assertTrue(s["input"]["includeSeen"])
        self.assertEqual(s["input"]["mode"], "following")
        self.assertEqual(s["query"]["maxTotalChargeUsd"], "0.05")
        self.assertEqual(s["query"]["restartOnError"], "false")

    def test_budget_never_resets_or_overbooks(self):
        with self.assertRaises(FileExistsError):
            self.budget.initialize("10")
        def reserve():
            try:
                self.budget.reserve("0.06")
                return True
            except ExperimentError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _: reserve(), range(2))), 1)

    def test_cap_and_cost_and_no_storage_reads(self):
        api = FakeAPI(metadata(), run())
        report = execute(self.plan, api, self.budget, authorized=True)
        self.assertIsNone(report["error"])
        self.assertEqual(report["cost_usd"], 0.01)
        self.assertEqual(api.calls[1][2]["maxTotalChargeUsd"], "0.05")
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(report["dataset_download"], "blocked_post_run_storage_cost_not_capped")
        self.assertFalse(report["result"]["integrity_verified"])

    def test_unknown_post_is_not_retried_and_reservation_stays(self):
        api = FakeAPI(metadata(), ExperimentError("network_error_no_automatic_retry"))
        report = execute(self.plan, api, self.budget, authorized=True)
        self.assertIsNone(report["run_id"])
        self.assertIsNone(report["cost_usd"])
        self.assertEqual(len(api.calls), 2)
        with self.assertRaises(ExperimentError):
            self.budget.reserve("0.06")

    def test_provider_minimum_never_silently_raises_cap(self):
        api = FakeAPI(metadata(0.20))
        result = execute(self.plan, api, self.budget, authorized=True)
        self.assertEqual(result["error"], "provider_minimum_exceeds_cap")
        self.assertEqual(len(api.calls), 1)

    def test_cap_mismatch_aborts(self):
        api = FakeAPI(metadata(), run("RUNNING", cap=1), run("ABORTED"))
        result = execute(self.plan, api, self.budget, authorized=True)
        self.assertEqual(result["error"], "server_budget_not_confirmed")
        self.assertTrue(api.calls[-1][1].endswith("/abort"))
        with self.assertRaisesRegex(ExperimentError, "suspended"):
            self.budget.reserve("0.01")

    def test_poll_failure_aborts_and_keeps_checkpoint(self):
        api = FakeAPI(metadata(), run("RUNNING"), ExperimentError("network_error_no_automatic_retry"), run("ABORTED"))
        snapshots = []
        result = execute(self.plan, api, self.budget, authorized=True, sleep=lambda _: None,
                         checkpoint=lambda r: snapshots.append(dict(r)))
        self.assertEqual(result["abort_status"], "ABORTED")
        self.assertTrue(any(r["run_id"] == "run123" for r in snapshots))

    def test_future_prices_not_effective_today(self):
        current = metadata()["data"]["pricingInfos"][0]
        future = dict(current, startedAt="2026-10-30T00:00:00Z", minimalMaxTotalChargeUsd=1)
        value = effective_pricing({"pricingInfos": [future, current]}, datetime(2026, 10, 18, tzinfo=timezone.utc))
        self.assertEqual(value["minimalMaxTotalChargeUsd"], 0.002)

    def test_normalize_two_providers_and_two_directions(self):
        for provider in ("apify", "steadyfetch"):
            for direction in ("followers", "following"):
                result = normalize(provider, direction, [row(provider, direction=direction)])
                self.assertEqual(result["recovered_count"], 1)
                self.assertFalse(result["real_identity_verified"])
                self.assertFalse(result["integrity_verified"])

    def test_errors_duplicates_wrong_source_and_bad_ids(self):
        rows = [row(), row(), dict(row(), sourceUsername="wrong"), dict(row(), userId=1.2),
                {"error": "no_items"}, {"status": "capped_by_instagram"}]
        result = normalize("apify", "followers", rows)
        self.assertEqual(result["recovered_count"], 1)
        self.assertEqual(len(result["issues"]), 5)

    def test_reference_detects_equal_counts_wrong_ids_and_names(self):
        result = normalize("apify", "followers", [row()])
        ref = {"username": TARGET, "complete": True, "observed_at": "2026-10-18T00:00:00Z",
               "source": "fictional unit test", "followers": [{"id": "1", "username": "fictional_one"}]}
        self.assertTrue(compare_reference(result, "followers", ref)["exact_match"])
        ref["followers"][0]["username"] = "different_name"
        self.assertEqual(compare_reference(result, "followers", ref)["username_mismatches"], ["1"])
        ref["followers"][0]["id"] = "2"
        compared = compare_reference(result, "followers", ref)
        self.assertEqual(compared["missing_ids"], ["2"])
        self.assertEqual(compared["unexpected_ids"], ["1"])

    def test_token_is_header_only_and_http_errors_are_sanitized(self):
        api = API("fictional_secret")
        with patch.object(api._opener, "open", side_effect=HTTPError("secret", 403, "fictional_secret", {}, None)) as mocked:
            with self.assertRaises(ExperimentError) as caught:
                api.request("GET", "acts/example")
        req = mocked.call_args[0][0]
        self.assertNotIn("fictional_secret", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Bearer fictional_secret")
        self.assertNotIn("fictional_secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
