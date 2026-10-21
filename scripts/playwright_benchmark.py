"""One authorized Stage 6 run, hard-limited to ten scroll cycles per list."""
import json

from backend.experimental.playwright_benchmark import run_benchmark


def main():
    output, result = run_benchmark()
    print(json.dumps({
        "report": str(output), "error": result["error"],
        "followers_cycles": len((result["followers"] or {}).get("cycles", [])),
        "following_cycles": len((result["following"] or {}).get("cycles", [])),
        "elapsed_seconds": result["elapsed_seconds"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
