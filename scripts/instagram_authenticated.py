"""Interactive login is separate from collection; no automatic login on expiry."""
import argparse
import contextlib
import getpass
import json
import os
import sys
import time
import uuid
from pathlib import Path

from backend.experimental.instagram_authenticated import AuthenticatedCollector, TARGET, error_code, make_client
from backend.experimental.instagram_session import ROOT, load_session, save_session, session_path
from backend.main import report
from backend.services.validation import structurally_consistent, validate_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Interactive: owner enters credentials in local terminal")
    collect = commands.add_parser("collect")
    collect.add_argument("--max-pages", type=int, default=1)
    collect.add_argument("--page-size", type=int, default=10)
    collect.add_argument("--repeat", action="store_true")
    collect.add_argument("--reference", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    if args.command == "collect" and not (1 <= args.max_pages <= 100 and 1 <= args.page_size <= 100):
        parser.error("max-pages and page-size must be 1..100")
    try:
        if args.command == "login":
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                parser.exit(2, "Execute login em um terminal local interativo.\n")
            session_path()  # Validate secure storage location before any network call.
            username = input("Username da sua conta secundaria (sem @): ").strip().lower()
            from backend.collector.instagram import normalize_username
            username = normalize_username(username)
            if username == TARGET:
                parser.exit(2, "Use sua conta secundaria, nao a conta alvo.\n")
            password = getpass.getpass("Senha (entrada oculta): ")
            code = getpass.getpass("Codigo 2FA atual, se habilitado (Enter se nao): ")
            # Suppress dependency output, which may contain response bodies.
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                client = make_client()
                try:
                    if not client.login(username, password, verification_code=code):
                        raise RuntimeError("login_failed")
                    account = client.account_info()
                    if account.username.lower() != username:
                        raise RuntimeError("session_identity_mismatch")
                    save_session(client.get_settings(), username)
                finally:
                    client.password = None
                    password = code = None
            print("Sessao criptografada salva em " + str(session_path()))
            return 0
        reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
        saved = load_session()
        if saved["secondary_username"].lower() == TARGET:
            raise RuntimeError("secondary_account_required")
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            client = make_client(saved["settings"])
            # Validate saved session directly; never call login() on reuse.
            account = client.account_info()
            if account.username.lower() != saved["secondary_username"]:
                raise RuntimeError("session_identity_mismatch")
            collector = AuthenticatedCollector(client, args.max_pages, args.page_size)
            first = collector.collect()
            runs = [first]
            verified = False
            if args.repeat and structurally_consistent(first):
                second = collector.collect()
                runs.append(second)
                if reference is not None:
                    verified = validate_reference(first, second, reference)
        output = {"method": "instagrapi_authenticated", "library_version": "3.0.26",
                  "integrity_verified": verified, "reference_supplied": reference is not None,
                  "repeat_requested": args.repeat, "runs": [report(r) for r in runs]}
        path = ROOT / "reports" / ("instagram-auth-" + uuid.uuid4().hex + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(path), "error": first.error,
                          "followers": first.followers.recovered_count,
                          "following": first.following.recovered_count,
                          "integrity_verified": verified}, indent=2))
        return 0 if verified else 2
    except KeyboardInterrupt:
        print("Interrompido; nenhuma nova tentativa automatica.", file=sys.stderr)
        return 2
    except Exception as exc:
        code = error_code(exc)
        path = ROOT / "reports" / ("instagram-auth-error-" + uuid.uuid4().hex + ".json")
        try:
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"operation": args.command, "error": code,
                                       "elapsed_seconds": round(time.monotonic() - started, 3),
                                       "integrity_verified": False, "non_followers": None}, indent=2), encoding="utf-8")
        except OSError:
            pass
        print("Falha: " + code + ". Consulte docs/authenticated-instagram.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
