#!/usr/bin/env python3
"""Print the current dashboard access code (Google Authenticator TOTP).

The dashboard's human login uses Google Authenticator (TOTP RFC 6238). To inspect
the current rolling 6-digit code from the terminal, run this:

    python3 scripts/dashboard_access_code.py

To scan the QR code into the Google Authenticator app on your phone, run:

    python3 scripts/setup_google_authenticator.py

The secret is read from, in order:

1. ``--secret-file PATH`` — explicit, and an unreadable file is an error
   rather than a silent fallback;
2. ``$DASHBOARD_ACCESS_SECRET``;
3. ``~/.tradingbot/dashboard_access_secret``.

The derivation is the *server's* — imported from ``access_code.py``, not
re-implemented here — so the code printed here and the code the server
accepts can never disagree.
"""

import argparse
import sys
from pathlib import Path

# The repo root, so the server's own module is importable from here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from access_code import SECRET_ENV_VAR, access_code, secret_from_env  # noqa: E402

DEFAULT_SECRET_FILE = Path.home() / ".tradingbot" / "dashboard_access_secret"


def read_secret_file(path: Path) -> str:
    """Read the secret from a file, trimmed. Empty string if it is absent."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def resolve_secret(secret_file: str = None, env=None) -> str:
    """The secret, and where it came from.

    Returns ``(secret, source)``. An explicitly named file that cannot be
    read is an error, not a fallback — silently using a different secret
    would print a code the server does not accept.
    """
    if secret_file:
        path = Path(secret_file).expanduser()
        from_file = read_secret_file(path)
        if not from_file:
            raise FileNotFoundError(f"no secret in {path}")
        return from_file, str(path)

    from_env = secret_from_env(env)
    if from_env:
        return from_env, SECRET_ENV_VAR

    from_default = read_secret_file(DEFAULT_SECRET_FILE)
    if from_default:
        return from_default, str(DEFAULT_SECRET_FILE)

    return "", "none"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Print today's dashboard access code.",
    )
    parser.add_argument(
        "--secret-file",
        default=None,
        help="Path to the file holding the access secret "
             f"(default: {DEFAULT_SECRET_FILE}).",
    )
    parser.add_argument(
        "--show-secret-source",
        action="store_true",
        help="Print where the secret came from (never the secret itself).",
    )
    args = parser.parse_args(argv)

    try:
        secret, source = resolve_secret(args.secret_file)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    if args.show_secret_source:
        print(f"secret source: {source}", file=sys.stderr)

    if not secret:
        print(
            "⚠️  No access secret configured - today's code is the date itself "
            "(ddmmyy), which anybody can derive.",
            file=sys.stderr,
        )
    print(access_code(secret))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
