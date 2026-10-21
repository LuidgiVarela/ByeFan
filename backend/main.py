import argparse
import json
import logging
from dataclasses import asdict

from backend.collector.instagram import InstagramCollector, normalize_username
from backend.services.comparator import InsufficientData, non_followers


def report(collection):
    output = asdict(collection)
    for kind in ("followers", "following"):
        output[kind]["recovered_count"] = getattr(collection, kind).recovered_count
    try:
        output["non_followers"] = [asdict(u) for u in non_followers(collection.followers, collection.following)]
        output["comparison_status"] = "verified"
    except InsufficientData:
        output["non_followers"] = None
        output["comparison_status"] = "insufficient_data"
    return output


def main():
    parser = argparse.ArgumentParser(description="ByeFan anonymous feasibility probe")
    parser.add_argument("username", type=normalize_username)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 100:
        parser.error("--max-pages must be between 1 and 100")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    result = report(InstagramCollector(max_pages=args.max_pages).collect(args.username))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["comparison_status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
