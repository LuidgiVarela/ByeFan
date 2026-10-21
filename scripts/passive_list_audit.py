"""Dry-run by default; --execute needs a separate owner authorization."""

import argparse
import json

from backend.experimental.passive_list_audit import run_once
from backend.experimental.playwright_browser import OBSERVER, TARGET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Open Instagram for one authorized limited UI audit")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "observer": OBSERVER,
                          "target": TARGET, "max_visible_per_list": 10,
                          "additional_requests": 0}, ensure_ascii=False))
        return
    output, result = run_once()
    print(json.dumps({"report": str(output), "error": result["error"],
                      "followers_visible": len((result["followers"] or {}).get("ui", {}).get("rows", [])),
                      "following_visible": len((result["following"] or {}).get("ui", {}).get("rows", [])),
                      "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
