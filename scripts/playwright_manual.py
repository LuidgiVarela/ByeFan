"""Run `launch`, then confirm only after the owner confirms in chat."""
import argparse
import json
from pathlib import Path

from backend.experimental.playwright_browser import CONTROL, approved, launch, status_path


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("launch")
    confirm = sub.add_parser("confirm")
    confirm.add_argument("run_id")
    args = parser.parse_args()
    if args.command == "launch":
        launch()
        return 0
    status = json.loads(status_path().read_text(encoding="utf-8"))
    if status["stage"] != "awaiting_owner_confirmation" or status["run_id"] != args.run_id:
        parser.error("No matching browser awaiting confirmation")
    marker = CONTROL / ("confirm-" + args.run_id + ".json")
    marker.write_text(json.dumps({"run_id": args.run_id}), encoding="utf-8")
    if not approved(args.run_id):
        parser.error("confirmation marker failed")
    print("Confirmation delivered to browser process.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
