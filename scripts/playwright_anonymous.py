"""Opt-in anonymous UI probe; never run before the owner's live-test approval."""
import json

from backend.experimental.playwright_anonymous import run_anonymous_sample


def main():
    output, result = run_anonymous_sample()
    print(json.dumps({"report": str(output), "error": result["error"],
                      "followers": len((result["followers"] or {}).get("rows", [])),
                      "following": len((result["following"] or {}).get("rows", [])),
                      "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
