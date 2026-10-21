"""One bounded passive-scroll audit; dry-run by default."""
import argparse
import json

from backend.experimental.passive_scroll_audit import MAX_CYCLES_PER_LIST, run_once
from backend.experimental.playwright_browser import OBSERVER, TARGET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "observer": OBSERVER, "target": TARGET,
                          "max_cycles_per_list": MAX_CYCLES_PER_LIST,
                          "explicit_private_requests": 0}, ensure_ascii=False))
        return
    path, result = run_once()
    print(json.dumps({"report": str(path), "error": result["error"],
                      "elapsed_seconds": result["elapsed_seconds"],
                      "followers": len((result["followers"] or {}).get("structured_rows", [])),
                      "following": len((result["following"] or {}).get("structured_rows", []))},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
