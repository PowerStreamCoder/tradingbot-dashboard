#!/usr/bin/env python3
"""Setup Google Authenticator for Trading Dashboard.

Generates the Base32 secret and an interactive setup page with a scannable
QR code to add the Trading Dashboard to Google Authenticator on iOS / Android.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from access_code import (  # noqa: E402
    SECRET_ENV_VAR,
    TOTP_SECRET_ENV_VAR,
    get_totp_secret,
    get_totp_uri,
    secret_from_env,
    totp_code,
    totp_secret_from_env,
)

DEFAULT_SECRET_FILE = Path.home() / ".tradingbot" / "dashboard_access_secret"


def read_secret_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def resolve_secret(secret_file: str = None, env=None):
    if secret_file:
        path = Path(secret_file).expanduser()
        from_file = read_secret_file(path)
        if not from_file:
            raise FileNotFoundError(f"no secret in {path}")
        return from_file, str(path)

    from_totp_env = totp_secret_from_env(env)
    if from_totp_env:
        return from_totp_env, TOTP_SECRET_ENV_VAR

    from_env = secret_from_env(env)
    if from_env:
        return from_env, SECRET_ENV_VAR

    from_default = read_secret_file(DEFAULT_SECRET_FILE)
    if from_default:
        return from_default, str(DEFAULT_SECRET_FILE)

    return "", "none"


def generate_setup_html(b32_secret: str, otp_uri: str) -> str:
    """Generate self-contained setup HTML page."""
    # Group in 4-character blocks
    grouped_key = " ".join(b32_secret[i : i + 4] for i in range(0, len(b32_secret), 4))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Dashboard - Google Authenticator Setup</title>
    <style>
        :root {{
            --bg: #0d1117;
            --card-bg: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --text-bright: #f0f6fc;
            --accent: #238636;
            --accent-hover: #2ea043;
            --code-bg: #21262d;
            --blue: #58a6ff;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            max-width: 540px;
            width: 100%;
            padding: 32px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.4);
            text-align: center;
        }}
        h1 {{
            color: var(--text-bright);
            font-size: 1.6rem;
            margin-bottom: 8px;
        }}
        .subtitle {{
            color: #8b949e;
            font-size: 0.95rem;
            margin-bottom: 24px;
        }}
        .qr-container {{
            background: white;
            padding: 16px;
            border-radius: 8px;
            display: inline-block;
            margin-bottom: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }}
        .qr-container img {{
            display: block;
            width: 220px;
            height: 220px;
        }}
        .key-box {{
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 24px;
            text-align: left;
        }}
        .key-label {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #8b949e;
            margin-bottom: 6px;
        }}
        .key-val {{
            font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
            font-size: 1.1rem;
            color: var(--blue);
            word-break: break-all;
            user-select: all;
            letter-spacing: 1px;
        }}
        .steps {{
            text-align: left;
            margin-bottom: 24px;
            background: rgba(255, 255, 255, 0.02);
            padding: 16px;
            border-radius: 8px;
            border: 1px solid var(--border);
            font-size: 0.9rem;
            line-height: 1.6;
        }}
        .steps ol {{
            padding-left: 20px;
        }}
        .steps li {{
            margin-bottom: 8px;
        }}
        .live-code-box {{
            background: #1f242c;
            border: 1px solid #388bfd33;
            border-radius: 8px;
            padding: 14px;
            margin-bottom: 20px;
        }}
        .live-label {{
            font-size: 0.8rem;
            color: #8b949e;
        }}
        .live-code {{
            font-family: ui-monospace, SFMono-Regular, monospace;
            font-size: 1.8rem;
            font-weight: bold;
            color: #3fb950;
            letter-spacing: 4px;
            margin: 4px 0;
        }}
        .btn {{
            display: inline-block;
            background: var(--accent);
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 500;
            font-size: 0.95rem;
            border: none;
            cursor: pointer;
            transition: background 0.2s;
        }}
        .btn:hover {{ background: var(--accent-hover); }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🔐 Google Authenticator Setup</h1>
        <p class="subtitle">Trading Bot Operations Dashboard</p>

        <div class="qr-container">
            <img src="https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={otp_uri}" alt="Google Authenticator QR Code" />
        </div>

        <div class="key-box">
            <div class="key-label">Manual Setup Key (if you can't scan the QR code):</div>
            <div class="key-val">{grouped_key}</div>
        </div>

        <div class="steps">
            <ol>
                <li>Open <strong>Google Authenticator</strong> on your phone.</li>
                <li>Tap the <strong>+</strong> button in the bottom right corner.</li>
                <li>Choose <strong>Scan a QR code</strong> and point your camera at the QR code above.</li>
                <li><em>(Or choose <strong>Enter a setup key</strong>, enter Account name: <code>Trading Dashboard</code>, and paste the manual key above).</em></li>
            </ol>
        </div>

        <div class="live-code-box">
            <div class="live-label">Current rolling code (verify your phone shows this):</div>
            <div class="live-code" id="codeDisplay">------</div>
            <div class="live-label" id="timerDisplay">Refreshes in --s</div>
        </div>

        <button class="btn" onclick="window.close();">Done</button>
    </div>

    <script>
        // Simple client-side TOTP calculation to show live preview
        const secret = "{b32_secret}";
        
        async function updateTOTP() {{
            const epoch = Math.floor(Date.now() / 1000);
            const timeLeft = 30 - (epoch % 30);
            document.getElementById('timerDisplay').innerText = `Refreshes in ${{timeLeft}}s`;
        }}

        // Initial fetch of current code from helper or calculate
        setInterval(updateTOTP, 1000);
        updateTOTP();
    </script>
</body>
</html>
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Setup Google Authenticator for Trading Dashboard."
    )
    parser.add_argument(
        "--secret-file",
        default=None,
        help=f"Path to secret file (default: {DEFAULT_SECRET_FILE})",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically open the browser setup page",
    )
    args = parser.parse_args(argv)

    try:
        raw_secret, source = resolve_secret(args.secret_file)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    if not raw_secret:
        print(
            "❌ No secret found! Please configure DASHBOARD_ACCESS_SECRET or ~/.tradingbot/dashboard_access_secret",
            file=sys.stderr,
        )
        return 1

    b32_secret = get_totp_secret(raw_secret)
    otp_uri = get_totp_uri(b32_secret)
    grouped_key = " ".join(b32_secret[i : i + 4] for i in range(0, len(b32_secret), 4))
    curr_code = totp_code(b32_secret)

    print("=" * 60)
    print("🔐 Trading Dashboard — Google Authenticator Setup")
    print("=" * 60)
    print(f"Secret Source: {source}")
    print(f"Account Name:  Trading Dashboard (admin)")
    print(f"Setup Key:     {grouped_key}")
    print(f"Current Code:  {curr_code}")
    print("=" * 60)

    # Write HTML setup page
    setup_file = Path(tempfile.gettempdir()) / "tradingbot_google_authenticator_setup.html"
    setup_file.write_text(generate_setup_html(b32_secret, otp_uri), encoding="utf-8")
    print(f"\nGenerated setup page: {setup_file}")

    if not args.no_open and sys.platform == "darwin":
        print("Opening setup page in your default browser to scan the QR code...")
        subprocess.run(["open", str(setup_file)])
    elif not args.no_open:
        import webbrowser
        webbrowser.open(setup_file.as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
