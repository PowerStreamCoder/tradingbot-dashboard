"""The dashboard's daily access code — one definition, two users.

The dashboard's human login uses a code that changes every day. Two things
need it:

* the **server**, when it checks what the operator typed at ``/authenticate``;
* the **operator**, who needs today's code to log in.

Those two must never disagree, so the derivation lives here and nowhere else
(D15/D16: resolve every live claim through the production code path). The
operator's helper script — ``scripts/dashboard_access_code.py`` — imports this
module rather than re-implementing the derivation.

Why not the date alone
----------------------
The original code was today's date in ``ddmmyy`` format. That is a *shared
convention*, not a secret: anyone who knows the dashboard exists and can read
the source can derive today's code and log in. A code that can be guessed is
not a credential.

With ``DASHBOARD_ACCESS_SECRET`` set, the code becomes
``HMAC-SHA256(secret, date)[:6]``: still one code per day, still printed by
the operator's helper script, but no longer derivable from the date alone.
The secret is the credential; the date is only the input.

Without the secret the legacy date code is used unchanged, so a missing
configuration can never lock the operator out — but it also never protects
anything. The helper script says so explicitly rather than pretending.
"""

import hashlib
import hmac
import os
from datetime import datetime, timezone

#: Environment variable holding the secret the daily code is derived from.
SECRET_ENV_VAR = "DASHBOARD_ACCESS_SECRET"

#: Length of the derived code, in characters.
CODE_LENGTH = 6

#: Date format of the date component of the code.
DATE_FORMAT = "%d%m%y"


def today_code(now=None) -> str:
    """Today's date as ``ddmmyy``, in UTC.

    Cloud Run runs in UTC, so this is the server's local date too. Being
    explicit about UTC matters because the operator's helper script runs on
    *their* machine, in *their* timezone: without it the two could disagree
    by a day for the hours around midnight.
    """
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.strftime(DATE_FORMAT)


def access_code(secret: str = "", now=None) -> str:
    """Today's access code.

    With no secret: the legacy ``ddmmyy`` date code.
    With a secret: ``HMAC-SHA256(secret, date)[:6]``, upper-cased.
    """
    date = today_code(now)
    if not secret:
        return date
    digest = hmac.new(
        secret.encode("utf-8"),
        date.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:CODE_LENGTH].upper()


def secret_from_env(env=None) -> str:
    """The configured secret, or ``""`` when none is configured."""
    environ = env if env is not None else os.environ
    return (environ.get(SECRET_ENV_VAR) or "").strip()


def code_matches(candidate, secret: str = "", now=None) -> bool:
    """Whether ``candidate`` is today's code — compared in constant time.

    Anything that is not a string is simply not a code.
    """
    if not isinstance(candidate, str):
        return False
    expected = access_code(secret, now=now)
    return hmac.compare_digest(
        candidate.strip().upper().encode("utf-8"), expected.encode("utf-8")
    )

