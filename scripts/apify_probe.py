"""Default action only prints a plan; no token or network needed."""
import argparse
import json
import os
from pathlib import Path

from backend.experimental.apify import API, ACTORS, Budget, ExperimentError, Plan, ROOT, compare_reference, execute, normalize
from backend.identity import TARGET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-budget", help="Local only; cannot overwrite an existing ledger")
    init.add_argument("--total-usd", required=True)
    probe = sub.add_parser("probe", help="Offline plan unless --execute-paid is supplied")
    probe.add_argument("provider", choices=ACTORS)
    probe.add_argument("direction", choices=("followers", "following"))
    probe.add_argument("--cap-usd", required=True)
    probe.add_argument("--limit", type=int, default=5)
    probe.add_argument("--expanded", action="store_true", help="Only after separate authorization to expand")
    probe.add_argument("--execute-paid", action="store_true", help="Only after explicit owner cost approval")
    check = sub.add_parser("check-reference", help="Offline comparison of saved report and independent reference")
    check.add_argument("report", type=Path)
    check.add_argument("reference", type=Path)
    analyze = sub.add_parser("analyze-export", help="Offline only; reads a previously obtained JSON dataset")
    analyze.add_argument("report", type=Path)
    analyze.add_argument("dataset", type=Path)
    analyze.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init-budget":
            Budget().initialize(args.total_usd)
            print("Local cumulative budget initialized; no network request.")
            return 0
        if args.command == "check-reference":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            reference = json.loads(args.reference.read_text(encoding="utf-8"))
            if report["plan"]["username"] != TARGET:
                raise ExperimentError("unauthorized_target")
            output = compare_reference(report["result"], report["plan"]["direction"], reference)
            output["run_succeeded"] = report["run_status"] == "SUCCEEDED"
            output["dataset_fully_downloaded"] = report["dataset_fully_downloaded"]
            print(json.dumps(output, indent=2))
            return 0 if all(output[k] for k in ("exact_match", "run_succeeded")) else 2
        if args.command == "analyze-export":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            p = report["plan"]
            Plan(p["provider"], p["direction"], p["query"]["maxTotalChargeUsd"],
                 p["input"]["resultsLimit"], username=p["username"], expanded=not p["sample"])
            if args.dataset.stat().st_size > 5_000_000:
                raise ExperimentError("export_too_large")
            rows = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
            if not isinstance(rows, list) or len(rows) > p["input"]["resultsLimit"] + 100:
                raise ExperimentError("unexpected_export_size")
            report["result"] = normalize(p["provider"], p["direction"], rows)
            report["dataset_origin"] = "local_export_provenance_and_exhaustion_not_independently_verified"
            report["local_export_rows"] = len(rows)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            print(json.dumps({"output": str(args.output), "recovered": report["result"]["recovered_count"],
                              "integrity_verified": False}, indent=2))
            return 0
        plan = Plan(args.provider, args.direction, args.cap_usd, args.limit, expanded=args.expanded)
        if not args.execute_paid:
            print(json.dumps({"execution": "NOT_STARTED", "network_requests": 0,
                              "cost_usd": 0, "plan": plan.preview()}, indent=2))
            return 0
        api = API(os.environ.get("APIFY_TOKEN"))
        def checkpoint(report):
            target = ROOT / "reports" / ("apify-" + report["reservation_id"] + ".json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        report = execute(plan, api, Budget(), authorized=True, checkpoint=checkpoint)
        target = ROOT / "reports" / ("apify-" + report["reservation_id"] + ".json")
        print(json.dumps({"report": str(target), "error": report["error"],
                          "recovered": report["result"]["recovered_count"],
                          "integrity_verified": False}, indent=2))
        return 2 if report["error"] or report["result"]["issues"] else 0
    except (ExperimentError, OSError, ValueError, KeyError, TypeError):
        # No traceback or arbitrary exception message: those may contain secrets.
        parser.exit(2, "Configuration/input error; check arguments, token, budget ledger and reference schema.\n")


if __name__ == "__main__":
    raise SystemExit(main())
