"""Hybrid collector: dry-run, explicit single live run, or offline validation."""

import argparse
import json
from pathlib import Path

from backend.experimental.hybrid_collector import (
    MAX_CYCLES_PER_LIST, WORK_BUDGET_SECONDS, SCROLL_PAUSE_MS, SETTLE_SECONDS, run_once,
    validate_repeated_reports,
)
from backend.experimental.playwright_browser import OBSERVER, TARGET
from backend.services.coverage import report_coverage


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true",
                        help="One live UI traversal; requires prior owner authorization")
    mode.add_argument("--validate", nargs=3, type=Path,
                        metavar=("FIRST", "SECOND", "REFERENCE"),
                        help="Offline comparison of two reports and independent owner IDs")
    mode.add_argument("--analyze", type=Path, help="Offline coverage analysis of a saved report")
    parser.add_argument("--coverage-target", type=int, choices=(98, 99, 100), default=98,
                        help="Partial coverage stopping target; 100 preserves full traversal mode")
    parser.add_argument("--viewport-height", type=int, choices=(800, 1000, 1200), default=800,
                        help="Experimental visible viewport height; defaults unchanged; live run needs authorization")
    args = parser.parse_args()
    if args.analyze:
        report = json.loads(args.analyze.read_text(encoding="utf-8"))
        print(json.dumps(report_coverage(report, args.coverage_target), ensure_ascii=False))
    elif args.validate:
        first, second, reference = (json.loads(path.read_text(encoding="utf-8"))
                                    for path in args.validate)
        print(json.dumps(validate_repeated_reports(first, second, reference),
                         ensure_ascii=False))
    elif args.execute:
        path, checkpoint, report = run_once(args.coverage_target, viewport_height=args.viewport_height)
        print(json.dumps({"report": str(path), "checkpoint": str(checkpoint),
                          "error": report["error"],
                          "followers": len((report["followers"] or {}).get(
                              "structured_rows", [])),
                          "following": len((report["following"] or {}).get(
                              "structured_rows", [])),
                          "elapsed_seconds": report["elapsed_seconds"],
                          "coverage": report.get("coverage"),
                          "comparison_status": report["comparison_status"]},
                         ensure_ascii=False))
    else:
        print(json.dumps({"mode": "dry_run", "target": TARGET, "observer": OBSERVER,
                          "max_cycles_per_list": MAX_CYCLES_PER_LIST,
                          "work_budget_seconds": WORK_BUDGET_SECONDS,
                          "minimum_scroll_pause_ms": SCROLL_PAUSE_MS,
                          "passive_settle_seconds": SETTLE_SECONDS,
                          "coverage_target_percent": args.coverage_target,
                          "viewport": {"width": 390, "height": args.viewport_height},
                          "definitive_comparison_allowed": False,
                          "explicit_private_requests": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
