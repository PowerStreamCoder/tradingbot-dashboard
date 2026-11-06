"""
Unit tests for Bot Selector status indicators and synchronization.
Verifies that bot pause/active state is clearly visualized and synchronized between
the top bot selector trigger pill and all dropdown option cards.
"""

from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient

from main import app, dashboard_data, authenticated_sessions

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"


def test_bot_focus_markup_has_status_pill():
    """Verify that bot_focus.html contains the status pill, indicator dot, and text label."""
    html_path = TEMPLATES_DIR / "bot_focus.html"
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    assert 'id="customBotSelect"' in content
    assert 'id="customBotStatusPill"' in content
    assert 'id="customBotIndicator"' in content
    assert 'id="customBotStatusLabel"' in content


def test_bot_focus_css_has_status_pill_styles():
    """Verify that bot_focus.css includes styling for paused and active states and animations."""
    css_path = STATIC_DIR / "css" / "bot_focus.css"
    assert css_path.exists()
    css_content = css_path.read_text(encoding="utf-8")

    assert ".custom-bot-status-pill" in css_content
    assert ".custom-bot-status-pill.active" in css_content
    assert ".custom-bot-status-pill.paused" in css_content
    assert ".custom-bot-status-pill.disabled" in css_content
    assert ".custom-bot-status-label" in css_content
    assert "pausePulse" in css_content


def test_bot_configs_merges_live_bot_status_paused():
    """Verify that /api/bot-configs merges is_trading=False into paused=True and status='paused'."""
    client = TestClient(app)

    mock_doc = MagicMock()
    mock_doc.id = "iwm"
    mock_doc.to_dict.return_value = {
        "symbol": "IWM",
        "is_trading": False,
        "market_status": "open"
    }

    mock_db = MagicMock()
    def mock_collection(name):
        col_mock = MagicMock()
        if name == "bot_status":
            col_mock.stream.return_value = [mock_doc]
        else:
            col_mock.stream.return_value = []
        return col_mock

    mock_db.collection.side_effect = mock_collection

    with patch("main.dashboard_data.db", mock_db):
        res = client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        iwm = next((b for b in data["bots"] if b.get("symbol") == "IWM"), None)
        assert iwm is not None
        assert iwm["is_trading"] is False
        assert iwm["paused"] is True
        assert iwm["status"] == "paused"


def test_bot_configs_merges_live_bot_status_active():
    """Verify that /api/bot-configs merges is_trading=True into paused=False and status='active'."""
    client = TestClient(app)

    mock_doc = MagicMock()
    mock_doc.id = "nvda"
    mock_doc.to_dict.return_value = {
        "symbol": "NVDA",
        "is_trading": True,
        "market_status": "open"
    }

    mock_db = MagicMock()
    def mock_collection(name):
        col_mock = MagicMock()
        if name == "bot_status":
            col_mock.stream.return_value = [mock_doc]
        else:
            col_mock.stream.return_value = []
        return col_mock

    mock_db.collection.side_effect = mock_collection

    with patch("main.dashboard_data.db", mock_db):
        res = client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        nvda = next((b for b in data["bots"] if b.get("symbol") == "NVDA"), None)
        assert nvda is not None
        assert nvda["is_trading"] is True
        assert nvda["paused"] is False
        assert nvda["status"] == "active"


def test_bot_configs_stale_heartbeat_marks_offline():
    """Verify that /api/bot-configs marks a bot with stale heartbeat (> 300s) as offline."""
    client = TestClient(app)

    mock_doc = MagicMock()
    mock_doc.id = "nvda"
    mock_doc.to_dict.return_value = {
        "symbol": "NVDA",
        "is_trading": True,
        "last_heartbeat": (datetime.now() - timedelta(minutes=10)).isoformat(),
        "market_status": "open"
    }

    mock_db = MagicMock()
    def mock_collection(name):
        col_mock = MagicMock()
        if name == "bot_status":
            col_mock.stream.return_value = [mock_doc]
        else:
            col_mock.stream.return_value = []
        return col_mock

    mock_db.collection.side_effect = mock_collection

    with patch("main.dashboard_data.db", mock_db):
        res = client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        nvda = next((b for b in data["bots"] if b.get("symbol") == "NVDA"), None)
        assert nvda is not None
        assert nvda["is_trading"] is False
        assert nvda["paused"] is True
        assert nvda["status"] == "offline"


def test_bot_configs_disabled_override_marks_disabled():
    """Verify that /api/bot-configs marks a bot with enabled=False as disabled."""
    client = TestClient(app)

    mock_rt_doc = MagicMock()
    mock_rt_doc.id = "afrm_sma"
    mock_rt_doc.to_dict.return_value = {
        "enabled": False,
        "profile": "paper"
    }

    mock_status_doc = MagicMock()
    mock_status_doc.id = "afrm"
    mock_status_doc.to_dict.return_value = {
        "symbol": "AFRM",
        "is_trading": True,
        "last_heartbeat": datetime.now().isoformat(),
        "market_status": "open"
    }

    mock_db = MagicMock()
    def mock_collection(name):
        col_mock = MagicMock()
        if name == "bot_runtime_controls":
            col_mock.stream.return_value = [mock_rt_doc]
        elif name == "bot_status":
            col_mock.stream.return_value = [mock_status_doc]
        else:
            col_mock.stream.return_value = []
        return col_mock

    mock_db.collection.side_effect = mock_collection

    with patch("main.dashboard_data.db", mock_db):
        res = client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        afrm = next((b for b in data["bots"] if b.get("name") == "afrm_sma"), None)
        assert afrm is not None
        assert afrm["enabled"] is False
        assert afrm["is_trading"] is False
        assert afrm["paused"] is True
        assert afrm["status"] == "disabled"


def test_bot_configs_firestore_exceptions_handled():
    """Verify that /api/bot-configs handles exceptions during Firestore queries gracefully."""
    client = TestClient(app)
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_col.stream.side_effect = RuntimeError("Firestore failure")
    mock_db.collection.return_value = mock_col

    with patch("main.dashboard_data.db", mock_db):
        res = client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        assert "bots" in data


def test_restart_bots_updates_status_docs():
    """Verify /api/bot-control/restart marks is_trading: True in bot_status on success."""
    client = TestClient(app)
    session_id = "test-status-restart-session"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=1)
    client.cookies.set("dashboard_session", session_id)

    mock_doc = MagicMock()
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_col.stream.return_value = [mock_doc]
    mock_db.collection.return_value = mock_col

    try:
        with patch("httpx.AsyncClient.post") as mock_post, patch("main.dashboard_data.db", mock_db):
            mock_post.return_value = MagicMock(
                is_success=True,
                status_code=200,
                json=lambda: {"status": "success", "message": "Bots restarted"}
            )
            res = client.post("/api/bot-control/restart")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            mock_doc.reference.update.assert_called_once()
            args, _ = mock_doc.reference.update.call_args
            assert args[0]["is_trading"] is True
    finally:
        authenticated_sessions.pop(session_id, None)


def test_restart_bots_handles_status_update_exception():
    """Verify /api/bot-control/restart still returns success if Firestore status update fails."""
    client = TestClient(app)
    session_id = "test-status-restart-err-session"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=1)
    client.cookies.set("dashboard_session", session_id)

    mock_doc = MagicMock()
    mock_doc.reference.update.side_effect = RuntimeError("DB write error")
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_col.stream.return_value = [mock_doc]
    mock_db.collection.return_value = mock_col

    try:
        with patch("httpx.AsyncClient.post") as mock_post, patch("main.dashboard_data.db", mock_db):
            mock_post.return_value = MagicMock(
                is_success=True,
                status_code=200,
                json=lambda: {"status": "success", "message": "Bots restarted"}
            )
            res = client.post("/api/bot-control/restart")
            assert res.status_code == 200
            assert res.json()["status"] == "success"
    finally:
        authenticated_sessions.pop(session_id, None)


def test_trade_history_all_doc_and_seq_mapping():
    """Verify doc_id, analysis_metadata trade_id, and seqNum mapping in /api/trade-history/all."""
    client = TestClient(app)
    session_id = "test-trades-map-session"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=1)
    client.cookies.set("dashboard_session", session_id)

    mock_doc = MagicMock()
    mock_doc.id = "mock_doc_id_999"
    mock_doc.to_dict.return_value = {
        "symbol": "AAPL",
        "timestamp": datetime.now().isoformat(),
        "analysis_metadata": {"trade_id": "meta_tid_123"},
        "sequence_number": 77
    }

    mock_query = MagicMock()
    mock_query.stream.return_value = [mock_doc]
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query

    mock_ref = MagicMock()
    mock_ref.where.return_value = mock_query

    try:
        with patch("main.dashboard_data.trades_ref", mock_ref):
            res = client.get("/api/trade-history/all?days=999")
            assert res.status_code == 200
            trades = res.json().get("trades", [])
            assert len(trades) >= 1
            t = trades[0]
            assert t["id"] == "mock_doc_id_999"
            assert t["trade_id"] == "meta_tid_123"
            assert t["seqNum"] == 77
    finally:
        authenticated_sessions.pop(session_id, None)
