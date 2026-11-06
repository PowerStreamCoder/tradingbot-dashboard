"""
Unit tests for Bot Selector status indicators and synchronization.
Verifies that bot pause/active state is clearly visualized and synchronized between
the top bot selector trigger pill and all dropdown option cards.
"""

from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from main import app, dashboard_data

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
