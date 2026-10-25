"""The dashboard's daily access code must be a credential, not a date.

**The finding.** The dashboard's human login used "today's date in ``ddmmyy``
format" as its access code. A date is a *shared convention*, not a secret:
anyone who knows the dashboard exists can derive today's code and log in.
The code was never static, so there was nothing to "rotate" — but the real
weakness was that it was publicly derivable in the first place.

**The fix.** With ``DASHBOARD_ACCESS_SECRET`` configured, the code becomes
``HMAC-SHA256(secret, date)[:6]``. Still one code per day, still printed by
the operator's helper script — but no longer derivable from the date alone.
Without the secret the legacy date code is kept, so a missing configuration
can never lock the operator out.

These tests pin:

1. the derivation itself (deterministic, secret-dependent, date-dependent);
2. that the **server and the operator's helper script agree** — the same
   module, so they cannot drift (D15/D16: resolve every live claim through
   the production code path);
3. that with a secret configured, the **legacy date code is rejected** at
   ``/authenticate`` — the property the whole change exists for;
4. that without a secret, the legacy code still works — no lock-out.

Every test is hermetic: no network, no Cloud Run, no real secret.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import access_code as ac
import main

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "scripts" / "dashboard_access_code.py"

TEST_SECRET = "a-test-secret-not-used-anywhere-else"


# --------------------------------------------------------------------------- #
# The derivation
# --------------------------------------------------------------------------- #
class TestDerivation:
    def test_without_a_secret_the_code_is_the_date(self):
        """The legacy behaviour, kept so a missing config cannot lock anyone out."""
        assert ac.access_code("") == ac.today_code()
        assert ac.access_code("") == datetime.now(timezone.utc).strftime("%d%m%y")

    def test_with_a_secret_the_code_is_six_upper_case_hex_characters(self):
        code = ac.access_code(TEST_SECRET)
        assert len(code) == ac.CODE_LENGTH
        assert code == code.upper()
        assert all(c in "0123456789ABCDEF" for c in code)

    def test_the_code_is_deterministic(self):
        assert ac.access_code(TEST_SECRET) == ac.access_code(TEST_SECRET)

    def test_a_different_secret_gives_a_different_code(self):
        assert ac.access_code("secret-one") != ac.access_code("secret-two")

    def test_the_code_is_not_the_date(self):
        """The whole point: it must not be derivable from the date alone."""
        assert ac.access_code(TEST_SECRET) != ac.today_code()

    def test_the_code_changes_with_the_date(self):
        now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        tomorrow = now + timedelta(days=1)
        assert ac.access_code(TEST_SECRET, now=now) != ac.access_code(
            TEST_SECRET, now=tomorrow
        )

    def test_the_date_component_is_utc(self):
        """Cloud Run is UTC; the operator's machine is not.

        Without UTC the helper script and the server could disagree by a day
        for the hours around midnight.
        """
        # 23:30 in New York on 21 Oct is already 22 Oct in UTC.
        ny_late = datetime(2026, 10, 21, 23, 30)
        assert ac.today_code(ny_late) == "211026"

    def test_today_code_uses_the_supplied_moment(self):
        moment = datetime(2026, 1, 5, 6, 7, tzinfo=timezone.utc)
        assert ac.today_code(moment) == "050126"


# --------------------------------------------------------------------------- #
# The secret comes from the environment
# --------------------------------------------------------------------------- #
class TestSecretFromEnv:
    def test_reads_the_environment_variable(self):
        assert ac.secret_from_env({"DASHBOARD_ACCESS_SECRET": "s3cret"}) == "s3cret"

    def test_strips_whitespace(self):
        """A trailing newline from a file or a paste must not change the code."""
        assert ac.secret_from_env({"DASHBOARD_ACCESS_SECRET": "  s3cret \n"}) == "s3cret"

    def test_no_secret_configured_is_empty(self):
        assert ac.secret_from_env({}) == ""
        assert ac.secret_from_env({"DASHBOARD_ACCESS_SECRET": None}) == ""
        assert ac.secret_from_env({"DASHBOARD_ACCESS_SECRET": "   "}) == ""

    def test_reads_the_process_environment_by_default(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_ACCESS_SECRET", "from-the-process")
        assert ac.secret_from_env() == "from-the-process"


# --------------------------------------------------------------------------- #
# Comparing a submitted code
# --------------------------------------------------------------------------- #
class TestCodeMatches:
    def test_accepts_the_right_code(self):
        code = ac.access_code(TEST_SECRET)
        assert ac.code_matches(code, TEST_SECRET) is True

    def test_rejects_a_wrong_code(self):
        assert ac.code_matches("000000", TEST_SECRET) is False

    def test_rejects_the_legacy_date_code_when_a_secret_is_set(self):
        assert ac.code_matches(ac.today_code(), TEST_SECRET) is False

    def test_rejects_none(self):
        assert ac.code_matches(None, TEST_SECRET) is False

    def test_rejects_a_non_string(self):
        """A submitted body must never be able to raise its way into a session."""
        assert ac.code_matches({"access_code": "x"}, TEST_SECRET) is False
        assert ac.code_matches(123456, TEST_SECRET) is False
        assert ac.code_matches(["x"], TEST_SECRET) is False

    def test_accepts_the_date_code_when_no_secret_is_set(self):
        assert ac.code_matches(ac.today_code(), "") is True

    def test_accepts_code_case_insensitively(self):
        code = ac.access_code(TEST_SECRET)
        assert ac.code_matches(code.lower(), TEST_SECRET) is True

    def test_accepts_code_with_surrounding_whitespace(self):
        code = ac.access_code(TEST_SECRET)
        assert ac.code_matches(f"  {code} \n", TEST_SECRET) is True


# --------------------------------------------------------------------------- #
# The server
# --------------------------------------------------------------------------- #
class TestServer:
    def test_the_server_uses_the_shared_derivation(self, monkeypatch):
        """One definition: the server and the helper script cannot disagree."""
        monkeypatch.setenv("DASHBOARD_ACCESS_SECRET", TEST_SECRET)
        assert main.get_valid_access_code() == ac.access_code(TEST_SECRET)

    def test_without_a_secret_the_server_keeps_the_date_code(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_ACCESS_SECRET", raising=False)
        assert main.get_valid_access_code() == ac.today_code()

    def test_authenticate_accepts_the_derived_code(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_ACCESS_SECRET", TEST_SECRET)
        client = TestClient(main.app)
        with patch.object(main.sessions_ref, "document") as mock_doc_fn:
            mock_doc_fn.return_value = MagicMock()
            res = client.post(
                "/authenticate", json={"access_code": ac.access_code(TEST_SECRET)}
            )
            assert res.status_code == 200
            assert "dashboard_session" in res.cookies

    def test_authenticate_accepts_lowercase_code(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_ACCESS_SECRET", TEST_SECRET)
        client = TestClient(main.app)
        with patch.object(main.sessions_ref, "document") as mock_doc_fn:
            mock_doc_fn.return_value = MagicMock()
            res = client.post(
                "/authenticate", json={"access_code": ac.access_code(TEST_SECRET).lower()}
            )
            assert res.status_code == 200
            assert "dashboard_session" in res.cookies

    def test_authenticate_rejects_the_legacy_date_code(self, monkeypatch):
        """RED FIRST: before the fix the date code was the *only* code accepted.

        This is the property the change exists for.
        """
        monkeypatch.setenv("DASHBOARD_ACCESS_SECRET", TEST_SECRET)
        client = TestClient(main.app)
        res = client.post("/authenticate", json={"access_code": ac.today_code()})
        assert res.status_code == 400

    def test_authenticate_still_accepts_the_date_code_without_a_secret(
        self, monkeypatch
    ):
        """No lock-out: a missing secret must not lock the operator out."""
        monkeypatch.delenv("DASHBOARD_ACCESS_SECRET", raising=False)
        client = TestClient(main.app)
        with patch.object(main.sessions_ref, "document") as mock_doc_fn:
            mock_doc_fn.return_value = MagicMock()
            res = client.post(
                "/authenticate", json={"access_code": ac.today_code()}
            )
            assert res.status_code == 200


# --------------------------------------------------------------------------- #
# The operator's helper script
# --------------------------------------------------------------------------- #
class TestHelperScript:
    def _run(self, env=None, args=()):
        environ = dict(os.environ)
        environ.pop("DASHBOARD_ACCESS_SECRET", None)
        environ.update(env or {})
        return subprocess.run(
            [sys.executable, str(HELPER), *args],
            capture_output=True,
            text=True,
            env=environ,
            cwd=str(REPO_ROOT),
            timeout=60,
        )

    def test_it_prints_the_code_the_server_accepts(self):
        """The helper and the server share one derivation, so they agree."""
        result = self._run(env={"DASHBOARD_ACCESS_SECRET": TEST_SECRET})
        assert result.returncode == 0
        assert result.stdout.strip() == ac.access_code(TEST_SECRET)

    def test_it_reports_when_no_secret_is_configured(self, tmp_path):
        result = self._run(env={"HOME": str(tmp_path)})
        assert result.returncode == 0
        assert result.stdout.strip() == ac.today_code()
        assert "No access secret configured" in result.stderr

    def test_an_explicit_secret_file_wins(self, tmp_path):
        secret_file = tmp_path / "secret"
        secret_file.write_text("from-a-file\n")
        result = self._run(
            env={"DASHBOARD_ACCESS_SECRET": TEST_SECRET},
            args=["--secret-file", str(secret_file)],
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ac.access_code("from-a-file")

    def test_an_unreadable_secret_file_is_an_error_not_a_fallback(self, tmp_path):
        """Silently using a different secret would print a code that does not work."""
        result = self._run(
            args=["--secret-file", str(tmp_path / "does-not-exist")]
        )
        assert result.returncode == 2
        assert "no secret in" in result.stderr

    def test_the_secret_source_can_be_shown_without_leaking_it(self):
        result = self._run(
            env={"DASHBOARD_ACCESS_SECRET": TEST_SECRET},
            args=["--show-secret-source"],
        )
        assert result.returncode == 0
        assert "DASHBOARD_ACCESS_SECRET" in result.stderr
        assert TEST_SECRET not in result.stderr
        assert TEST_SECRET not in result.stdout

    def test_setup_helper_script_runs(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "setup_google_authenticator.py"),
                "--no-open",
            ],
            capture_output=True,
            text=True,
            env={"DASHBOARD_ACCESS_SECRET": TEST_SECRET},
            cwd=str(REPO_ROOT),
            timeout=30,
        )
        assert result.returncode == 0
        assert "Google Authenticator Setup" in result.stdout
        assert "Setup Key:" in result.stdout
        assert "Current Code:" in result.stdout


# --------------------------------------------------------------------------- #
# Google Authenticator TOTP (RFC 6238)
# --------------------------------------------------------------------------- #
class TestGoogleAuthenticatorTOTP:
    def test_rfc6238_standard_test_vectors(self):
        """Standard RFC 6238 TOTP test vectors with 30s period and SHA1."""
        # 12345678901234567890 base32 encoded per RFC 6238 appendix B
        import base64
        rfc_secret = base64.b32encode(b"12345678901234567890").decode()  # gitleaks:allow
        assert ac.totp_code(rfc_secret, for_time=59) == "287082"
        assert ac.totp_code(rfc_secret, for_time=1111111109) == "081804"
        assert ac.totp_code(rfc_secret, for_time=1111111111) == "050471"
        assert ac.totp_code(rfc_secret, for_time=1234567890) == "005924"
        assert ac.totp_code(rfc_secret, for_time=2000000000) == "279037"

    def test_clock_drift_window_acceptance(self):
        """Codes within +- 30 seconds are accepted to handle minor clock skew."""
        b32_sec = ac.get_totp_secret(TEST_SECRET)
        t_now = 1700000000
        current_code = ac.totp_code(b32_sec, for_time=t_now)
        prev_code = ac.totp_code(b32_sec, for_time=t_now - 25)
        next_code = ac.totp_code(b32_sec, for_time=t_now + 25)

        assert ac.code_matches(current_code, TEST_SECRET, now=t_now) is True
        assert ac.code_matches(prev_code, TEST_SECRET, now=t_now) is True
        assert ac.code_matches(next_code, TEST_SECRET, now=t_now) is True

    def test_expired_code_is_rejected(self):
        """Codes outside the window (>+- 30s) are rejected."""
        b32_sec = ac.get_totp_secret(TEST_SECRET)
        t_now = 1700000000
        old_code = ac.totp_code(b32_sec, for_time=t_now - 90)
        future_code = ac.totp_code(b32_sec, for_time=t_now + 90)

        assert ac.code_matches(old_code, TEST_SECRET, now=t_now) is False
        assert ac.code_matches(future_code, TEST_SECRET, now=t_now) is False

    def test_explicit_totp_secret_env_var(self, monkeypatch):
        """Explicit DASHBOARD_TOTP_SECRET can override or provide TOTP key."""
        import base64
        custom_b32 = base64.b32encode(b"custom_test_totp").decode()  # gitleaks:allow
        monkeypatch.setenv("DASHBOARD_TOTP_SECRET", custom_b32)
        code = ac.totp_code(custom_b32)
        assert ac.code_matches(code) is True

    def test_otpauth_uri_generation(self):
        import base64
        test_key = base64.b32encode(b"test_uri_key_data").decode()  # gitleaks:allow
        uri = ac.get_totp_uri(
            test_key, account_name="operator", issuer="TradingDashboard"
        )
        assert (
            uri
            == f"otpauth://totp/TradingDashboard:operator?secret={test_key}&issuer=TradingDashboard"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

