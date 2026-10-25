"""The dashboard's access code authentication — Google Authenticator (TOTP, RFC 6238).

The dashboard's human login uses Google Authenticator:
* the **server**, when it checks what the operator typed at ``/authenticate``;
* the **operator**, who reads the 6-digit rolling code from the Google Authenticator app on their phone.

The derivation is pure Python standard library (RFC 6238 TOTP with SHA1, standard 30s period,
6-digit output). The root secret is read from ``DASHBOARD_ACCESS_SECRET`` (or ``DASHBOARD_TOTP_SECRET``)
or ``~/.tradingbot/dashboard_access_secret``, deterministically converted to a Base32 key.

Without a configured secret, it falls back to the legacy date code so an unconfigured local
development environment or test run does not crash.
"""

import base64
import hashlib
import hmac
import os
import struct
import time
from datetime import datetime, timezone

#: Environment variable holding the root secret the TOTP key is derived from.
SECRET_ENV_VAR = "DASHBOARD_ACCESS_SECRET"

#: Optional environment variable holding an explicit Base32 TOTP secret.
TOTP_SECRET_ENV_VAR = "DASHBOARD_TOTP_SECRET"

#: Length of the derived code, in characters.
CODE_LENGTH = 6

#: Date format of the legacy date fallback.
DATE_FORMAT = "%d%m%y"

#: TOTP time step in seconds (standard RFC 6238).
TOTP_TIME_STEP = 30


def get_totp_secret(secret: str = "", totp_secret: str = "") -> str:
    """Get the standard Base32 TOTP key.

    If an explicit Base32 TOTP secret is supplied, it is cleaned and used directly.
    Otherwise, if a secret is provided, it is deterministically hashed with SHA-256
    and the first 20 bytes (160-bit standard for HMAC-SHA1) are Base32 encoded.
    """
    if totp_secret:
        return totp_secret.strip().upper().replace(" ", "")
    if not secret:
        return ""
    # Deterministic 160-bit (20 bytes) Base32 derivation:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()[:20]
    return base64.b32encode(digest).decode("utf-8").replace("=", "")


def totp_code(
    secret_b32: str,
    for_time=None,
    time_step: int = TOTP_TIME_STEP,
    digits: int = CODE_LENGTH,
) -> str:
    """Generate RFC 6238 TOTP code from a Base32 secret."""
    if not secret_b32:
        return ""
    if for_time is None:
        t = int(time.time())
    elif isinstance(for_time, (int, float)):
        t = int(for_time)
    elif isinstance(for_time, datetime):
        if for_time.tzinfo is None:
            for_time = for_time.replace(tzinfo=timezone.utc)
        t = int(for_time.timestamp())
    else:
        t = int(time.time())

    key = secret_b32.strip().upper().replace(" ", "")
    pad = len(key) % 8
    if pad:
        key += "=" * (8 - pad)
    key_bytes = base64.b32decode(key, casefold=True)

    counter = int(t // time_step)
    counter_bytes = struct.pack(">Q", counter)

    h = hmac.new(key_bytes, counter_bytes, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    binary = (
        ((h[offset] & 0x7F) << 24)
        | ((h[offset + 1] & 0xFF) << 16)
        | ((h[offset + 2] & 0xFF) << 8)
        | (h[offset + 3] & 0xFF)
    )
    otp = binary % (10 ** digits)
    return f"{otp:0{digits}d}"


def verify_totp(candidate: str, secret_b32: str, now=None, window: int = 1) -> bool:
    """Verify candidate code against TOTP within window (+- 1 step = 30s drift tolerance)."""
    if not candidate or not isinstance(candidate, str):
        return False
    cleaned = candidate.strip()
    if len(cleaned) != CODE_LENGTH or not cleaned.isdigit():
        return False
    if not secret_b32:
        return False

    if now is None:
        t = int(time.time())
    elif isinstance(now, (int, float)):
        t = int(now)
    elif isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        t = int(now.timestamp())
    else:
        t = int(time.time())

    matched = False
    for delta in range(-window, window + 1):
        step_time = t + (delta * TOTP_TIME_STEP)
        expected = totp_code(secret_b32, for_time=step_time)
        if hmac.compare_digest(cleaned, expected):
            matched = True
    return matched


def today_code(now=None) -> str:
    """Today's date as ``ddmmyy``, in UTC (kept as unconfigured fallback)."""
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.strftime(DATE_FORMAT)


def access_code(secret: str = "", now=None, totp_secret: str = "") -> str:
    """The current access code.

    With no secret: the legacy ``ddmmyy`` date code.
    With a secret: the 6-digit Google Authenticator TOTP code for the current 30s window.
    """
    if not secret and not totp_secret:
        return today_code(now)
    key_b32 = get_totp_secret(secret, totp_secret)
    return totp_code(key_b32, for_time=now)


def secret_from_env(env=None) -> str:
    """The configured secret, or ``""`` when none is configured."""
    environ = env if env is not None else os.environ
    return (environ.get(SECRET_ENV_VAR) or "").strip()


def totp_secret_from_env(env=None) -> str:
    """The configured explicit TOTP secret, or ``""`` when none is configured."""
    environ = env if env is not None else os.environ
    return (environ.get(TOTP_SECRET_ENV_VAR) or "").strip()


def code_matches(candidate, secret: str = None, now=None, totp_secret: str = None) -> bool:
    """Whether ``candidate`` is a valid Google Authenticator TOTP code.

    With no secret configured: falls back to the legacy date code.
    With a secret configured: requires a valid 6-digit Google Authenticator code.
    Compared in constant time with +-30s clock drift tolerance.
    """
    if not isinstance(candidate, str):
        return False

    cleaned = candidate.strip().upper()
    sec = secret_from_env() if secret is None else secret
    totp_sec = totp_secret_from_env() if totp_secret is None else totp_secret

    if not sec and not totp_sec:
        expected = today_code(now)
        return hmac.compare_digest(cleaned.encode("utf-8"), expected.encode("utf-8"))

    key_b32 = get_totp_secret(sec, totp_sec)
    return verify_totp(cleaned, key_b32, now=now)


def get_totp_uri(
    secret_b32: str,
    account_name: str = "admin",
    issuer: str = "TradingDashboard",
) -> str:
    """Generate the standard otpauth URI for Google Authenticator QR code."""
    return f"otpauth://totp/{issuer}:{account_name}?secret={secret_b32}&issuer={issuer}"
