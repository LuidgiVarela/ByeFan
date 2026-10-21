"""Compare saved authorized observations offline; never query Instagram."""
import argparse
import json
from pathlib import Path

from backend.services.relationships import analyze_history, analyze_snapshot


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--repeat', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--previous-repeat', type=Path)
    parser.add_argument('--previous-reference', type=Path)
    parser.add_argument('--output', type=Path, help='Private local JSON; default prints counts only')
    args = parser.parse_args()
    if bool(args.repeat) != bool(args.reference):
        parser.error('--repeat and --reference must be provided together')
    if bool(args.previous_repeat) != bool(args.previous_reference):
        parser.error('--previous-repeat and --previous-reference must be provided together')
    if not args.previous and (args.previous_repeat or args.previous_reference):
        parser.error('previous verification requires --previous')
    current = read(args.report)
    if args.previous:
        result = analyze_history(read(args.previous), current,
            previous_repeat=read(args.previous_repeat), previous_reference=read(args.previous_reference),
            current_repeat=read(args.repeat), current_reference=read(args.reference))
    else:
        result = analyze_snapshot(current, repeat=read(args.repeat), reference=read(args.reference))
    if args.output:
        # Keep personal result artifacts inside the ignored reports directory.
        output = args.output.resolve()
        root = Path(__file__).resolve().parents[1] / 'reports'
        if not output.is_relative_to(root.resolve()) or output == root.resolve():
            parser.error('--output must be a file under reports/')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'target': result['target'], 'observed_at': result['observed_at'],
        'counts': result['counts'], 'comparison_status': result['comparison_status'],
        'history_status': result['history']['status'], 'live_query_performed': False}))


if __name__ == '__main__':
    main()
