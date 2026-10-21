"""One authorized Stage 8 run; never retries or targets another profile."""
import argparse
import json
from pathlib import Path

from backend.experimental.playwright_integral import run_integral


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path,
                        help="Optional owner-provided independent username reference JSON")
    args = parser.parse_args()
    reference = None
    if args.reference:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        if not isinstance(reference, dict):
            parser.error("reference must be a JSON object")
    output, checkpoint, result = run_integral(reference)
    print(json.dumps({
        "report": str(output), "checkpoint": str(checkpoint),
        "error": result["error"],
        "followers": len((result["followers"] or {}).get("rows", [])),
        "following": len((result["following"] or {}).get("rows", [])),
        "elapsed_seconds": result["elapsed_seconds"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
