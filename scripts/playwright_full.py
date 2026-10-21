"""Single full-list experiment on the owner's authorized Instagram profile."""
import argparse
import json
from pathlib import Path

from backend.experimental.playwright_browser import run_full


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path,
                        help="Optional independent owner-provided username reference JSON")
    args = parser.parse_args()
    reference = None
    if args.reference:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        if not isinstance(reference, dict):
            parser.error("reference must be a JSON object")
    output, result = run_full(reference)
    print(json.dumps({"report": str(output), "error": result["error"],
                      "followers": len((result["followers"] or {}).get("rows", [])),
                      "following": len((result["following"] or {}).get("rows", [])),
                      "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
