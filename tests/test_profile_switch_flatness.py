"""
Unit tests for Issue #112 / Phase 4:
Firestore Overview Unification & Flatness Gate in /switch-profile.
Verifies fail-closed behavior, doc ID unification on 'bot_overview/current',
and equity shares + covered-call option contract enforcement.
"""

from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient

from main import app, authenticated_sessions


@pytest.fixture
def auth_client():
    client = TestClient(app)
    session_id = "test-session-flatness-gate"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=2)
    client.cookies.set("dashboard_session", session_id)
    yield client
    authenticated_sessions.pop(session_id, None)


def test_switch_profile_blocks_when_bot_overview_missing(auth_client):
    """Fail-closed: missing bot_overview/current must block profile switch."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_db.collection("bot_overview").document("current").get.return_value = (
        mock_doc
    )

    with patch("google.cloud.firestore.Client", return_value=mock_db):
        res = auth_client.post(
            "/api/bot-control/switch-profile", json={"profile": "live"}
        )
        assert res.status_code == 400
        data = res.json()
        assert "bot_overview/current document does not exist" in data["detail"]
        assert "fail-closed" in data["detail"].lower()


def test_switch_profile_blocks_when_firestore_raises_exception(auth_client):
    """Fail-closed: read exception on bot_overview/current must block switch."""
    mock_db = MagicMock()
    mock_db.collection("bot_overview").document("current").get.side_effect = (
        RuntimeError("Firestore timeout")
    )

    with patch("google.cloud.firestore.Client", return_value=mock_db):
        res = auth_client.post(
            "/api/bot-control/switch-profile", json={"profile": "live"}
        )
        assert res.status_code == 500
        data = res.json()
        assert "failed to query positions from firestore" in data["detail"].lower()
        assert "fail-closed" in data["detail"].lower()


def test_switch_profile_blocks_on_open_equity_shares_canonical(auth_client):
    """Blocks switch when canonical bot_overview/current has bucket shares."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "data": {
            "bot4": {
                "symbol": "NVDA",
                "name": "NVDA Bot",
                "bucket1": {
                    "entryPrice": 140.50,
                    "quantity": 100,
                    "shares": 100,
                    "optionSold": False,
                }
            }
        }
    }
    mock_db.collection("bot_overview").document("current").get.return_value = (
        mock_doc
    )

    with patch("google.cloud.firestore.Client", return_value=mock_db):
        res = auth_client.post(
            "/api/bot-control/switch-profile", json={"profile": "live"}
        )
        assert res.status_code == 400
        data = res.json()
        assert "1 open position(s)" in data["detail"]
        assert "NVDA" in data["detail"]


def test_switch_profile_blocks_on_open_option_contract(auth_client):
    """Blocks switch when covered call is open even if shares count is 0."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "data": {
            "bot5": {
                "symbol": "MSFT",
                "name": "MSFT Bot",
                "bucket1": {
                    "entryPrice": None,
                    "shares": 0,
                    "quantity": 0,
                    "optionSold": True,
                    "optionStrike": 450.0,
                    "optionState": "OPEN",
                }
            }
        }
    }
    mock_db.collection("bot_overview").document("current").get.return_value = (
        mock_doc
    )

    with patch("google.cloud.firestore.Client", return_value=mock_db):
        res = auth_client.post(
            "/api/bot-control/switch-profile", json={"profile": "live"}
        )
        assert res.status_code == 400
        data = res.json()
        assert "1 open position(s)" in data["detail"]
        assert "open option contract" in data["detail"]
        assert "MSFT" in data["detail"]


def test_switch_profile_passes_when_flat(auth_client):
    """Succeeds when bot_overview/current exists and has 0 shares and 0 options."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "data": {
            "bot4": {
                "symbol": "NVDA",
                "bucket1": {
                    "entryPrice": None,
                    "quantity": 0,
                    "shares": 0,
                    "optionSold": False,
                }
            },
            "bot5": {
                "symbol": "MSFT",
                "bucket1": {
                    "entryPrice": None,
                    "quantity": 0,
                    "shares": 0,
                    "optionSold": False,
                }
            }
        }
    }
    mock_db.collection("bot_overview").document("current").get.return_value = (
        mock_doc
    )

    with patch("google.cloud.firestore.Client", return_value=mock_db), \
         patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "success",
                "profile": "live",
                "profile_confirmed": True
            }
        )
        res = auth_client.post(
            "/api/bot-control/switch-profile", json={"profile": "live"}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "success"

