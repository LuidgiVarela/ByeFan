"""Offline by default. --execute requires a separately authorized single attempt."""
import argparse
import json

from backend.experimental.anonymous_inspection import (
    BROWSER_CHANNELS, VIEWPORTS, MAX_SECONDS, MAX_USERS, TARGET, run_once,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--attempt")
    parser.add_argument("--browser", choices=BROWSER_CHANNELS, default="chromium")
    parser.add_argument("--layout", choices=VIEWPORTS, default="narrow")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"mode": "offline_plan", "target": TARGET, "sample_per_list": MAX_USERS,
                          "work_budget_seconds": MAX_SECONDS, "network_access": False,
                          "browser": args.browser, "layout": args.layout,
                          "viewport": VIEWPORTS[args.layout]}))
        return
    if not args.attempt:
        parser.error("--execute requires --attempt")
    path, result = run_once(args.attempt, browser_name=args.browser, layout=args.layout)
    print(json.dumps({"report": str(path), "error": result.get("error"),
                      "outcome": result.get("outcome"),
                      "elapsed_seconds": result.get("elapsed_seconds"),
                      "followers": len((result.get("followers") or {}).get("rows", [])),
                      "following": len((result.get("following") or {}).get("rows", []))}))


if __name__ == "__main__":
    main()
