"""Run only against an owned/expressly authorized public account."""
import argparse
import json
from pathlib import Path

from backend.collector.instagram import InstagramCollector, normalize_username
from backend.main import report
from backend.services.validation import structurally_consistent, validate_reference


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username", type=normalize_username)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/live.json"))
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
    first = InstagramCollector().collect(args.username)
    runs = [first]
    verified = False
    # No retries or second transport after a refusal, partial data or page limit.
    if structurally_consistent(first):
        second = InstagramCollector().collect(args.username)
        runs.append(second)
        if reference is not None:
            verified = validate_reference(first, second, reference)
    output = {"success": verified, "reference_supplied": reference is not None,
              "repeat_executed": len(runs) == 2, "runs": [report(r) for r in runs]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
