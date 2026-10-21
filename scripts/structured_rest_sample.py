"""Stage 11 sample; dry-run by default and no automatic retry."""
import argparse
import json

from backend.experimental.structured_rest_browser import MAX_SAMPLE, run_once
from backend.experimental.playwright_browser import OBSERVER, TARGET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Make one authorized live attempt against Instagram")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "observer": OBSERVER, "target": TARGET,
                          "per_list_requested": MAX_SAMPLE, "requests_at_most": 3,
                          "live_access": False}, ensure_ascii=False))
        return
    path, result = run_once()
    print(json.dumps({"report": str(path), "error": result["error"],
                      "requests": len(result["requests"]),
                      "followers": len((result["followers"] or {}).get("rows", [])),
                      "following": len((result["following"] or {}).get("rows", [])),
                      "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
