"""One authorized intermediate benchmark, hard-limited to 40 scrolls per list."""
import json

from backend.experimental.playwright_benchmark import MAX_INTERMEDIATE_CYCLES, run_benchmark


def main():
    output, result = run_benchmark(max_cycles=MAX_INTERMEDIATE_CYCLES)
    print(json.dumps({
        "report": str(output), "error": result["error"],
        "followers_cycles": len((result["followers"] or {}).get("cycles", [])),
        "following_cycles": len((result["following"] or {}).get("cycles", [])),
        "elapsed_seconds": result["elapsed_seconds"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
