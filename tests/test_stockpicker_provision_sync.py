"""
Unit and regression tests for StockPicker candidate score preservation during provisioning
and automatic Bot Focus bot selector synchronization.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, mock_open
import pytest
from fastapi.testclient import TestClient

from main import app, authenticated_sessions, dashboard_data


@pytest.fixture
def auth_client():
    client = TestClient(app)
    session_id = "test-prov-sync-session"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=2)
    client.cookies.set("dashboard_session", session_id)
    return client


def test_provision_bot_preserves_score_and_metadata(auth_client, tmp_path):
    """
    Verify that when a bot is provisioned via POST /api/stock-picks/provision-bot,
    the candidate's composite_score, score, catalyst, evidence_dossier, and industry
    are preserved in already_accepted rather than stripped out to None / undefined.
    """
    fake_bots_json = tmp_path / "bots.json"
    fake_bots_json.write_text(json.dumps({
        "bots": [
            {"name": "qqq_sma", "symbol": "QQQ", "client_id": 7, "enabled": True}
        ]
    }))

    cand_data = {
        "ticker": "AFRM",
        "symbol": "AFRM",
        "composite_score": 80.74,
        "score": 80.74,
        "strategy_track": "GROWTH",
        "catalyst": "Explosive consumer lending growth and partnership expansion",
        "fundamental_reasons": "Explosive consumer lending growth",
        "industry": "Fintech / Payments",
        "evidence_dossier": {
            "industry": "Fintech / Payments",
            "explosiveness_score": 8.5
        },
        "current_price": 42.50,
        "est_atr_pct": 3.2
    }

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "actionable_leads": [cand_data],
        "growth_picks": [cand_data],
        "already_accepted": []
    }

    with patch("main.load_bot_configs"):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_bots_json.read_text())), \
             patch("os.makedirs"), \
             patch.object(dashboard_data.stock_picks_ref, "document") as mock_doc_ref, \
             patch("httpx.AsyncClient.post", return_value=MagicMock(status_code=200)):

            mock_doc_ref.return_value.get.return_value = mock_doc

            resp = auth_client.post("/api/stock-picks/provision-bot", json={
                "symbol": "AFRM",
                "strategy_track": "GROWTH",
                "capital_allocation": 10000.0
            })

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["symbol"] == "AFRM"
            assert data["client_id"] == 8

            # Verify that set() was called on Firestore with already_accepted retaining rich candidate fields
            assert mock_doc_ref.return_value.set.called
            saved_data = mock_doc_ref.return_value.set.call_args[0][0]
            already_acc = saved_data["already_accepted"]
            assert len(already_acc) == 1
            afrm_entry = already_acc[0]

            assert afrm_entry["symbol"] == "AFRM"
            assert afrm_entry["status"] == "PROVISIONED"
            assert afrm_entry["client_id"] == 8
            assert afrm_entry["composite_score"] == 80.74
            assert afrm_entry["score"] == 80.74
            assert afrm_entry["initial_score"] == 80.74
            assert afrm_entry["catalyst"] == "Explosive consumer lending growth and partnership expansion"
            assert afrm_entry["industry"] == "Fintech / Payments"
            assert afrm_entry["evidence_dossier"]["explosiveness_score"] == 8.5


def test_provision_bot_merges_existing_accepted_candidate(auth_client, tmp_path):
    """
    Verify that if candidate was already in already_accepted (e.g. accepted first),
    provisioning preserves composite_score, score, catalyst, and rich fields.
    """
    fake_bots_json = tmp_path / "bots.json"
    fake_bots_json.write_text(json.dumps({
        "bots": [
            {"name": "qqq_sma", "symbol": "QQQ", "client_id": 7, "enabled": True}
        ]
    }))

    existing_accepted = {
        "ticker": "AFRM",
        "symbol": "AFRM",
        "initial_score": 80.74,
        "composite_score": 80.74,
        "score": 80.74,
        "strategy_track": "GROWTH",
        "catalyst": "Explosive consumer lending growth",
        "status": "ACCEPTED"
    }

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "actionable_leads": [],
        "growth_picks": [],
        "already_accepted": [existing_accepted]
    }

    with patch("main.load_bot_configs"):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_bots_json.read_text())), \
             patch("os.makedirs"), \
             patch.object(dashboard_data.stock_picks_ref, "document") as mock_doc_ref, \
             patch("httpx.AsyncClient.post", return_value=MagicMock(status_code=200)):

            mock_doc_ref.return_value.get.return_value = mock_doc

            resp = auth_client.post("/api/stock-picks/provision-bot", json={
                "symbol": "AFRM",
                "strategy_track": "GROWTH",
                "capital_allocation": 10000.0
            })

            assert resp.status_code == 200
            saved_data = mock_doc_ref.return_value.set.call_args[0][0]
            afrm_entry = saved_data["already_accepted"][0]
            assert afrm_entry["status"] == "PROVISIONED"
            assert afrm_entry["composite_score"] == 80.74
            assert afrm_entry["score"] == 80.74
            assert afrm_entry["initial_score"] == 80.74


def test_frontend_score_fallback_logic():
    """
    Contract test: verifies static/js/stock_picker.js falls back to initial_score
    and current_score when composite_score/score are absent, preventing '-' or 'Speculative'
    rendering on provisioned candidate rows.
    """
    js_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "stock_picker.js")
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Must check initial_score and current_score
    assert "initial_score" in content
    assert "current_score" in content
    assert "rawScore" in content
    assert "pick.initial_score != null" in content


def test_frontend_bot_selector_synchronization():
    """
    Contract test: verifies that:
    1. stock_picker.js triggers window.refreshBotSelector on provision and navigateToBot
    2. bot_focus.js exports window.refreshBotSelector and window.loadBotConfig
    3. bot_focus.html calls refreshBotSelector when switching back to Bot Focus tab
    """
    sp_js_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "stock_picker.js")
    with open(sp_js_path, "r", encoding="utf-8") as f:
        sp_content = f.read()

    assert "window.refreshBotSelector" in sp_content
    # confirmProvisionBot must call refreshBotSelector
    assert "await window.refreshBotSelector(res.client_id)" in sp_content
    # navigateToBot must call refreshBotSelector
    assert "await window.refreshBotSelector" in sp_content

    bf_js_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "bot_focus.js")
    with open(bf_js_path, "r", encoding="utf-8") as f:
        bf_content = f.read()

    assert "window.refreshBotSelector = refreshBotSelector" in bf_content
    assert "window.loadBotConfig = loadBotConfig" in bf_content
    assert "async function refreshBotSelector" in bf_content

    bf_html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "bot_focus.html")
    with open(bf_html_path, "r", encoding="utf-8") as f:
        bf_html_content = f.read()

    assert "window.refreshBotSelector()" in bf_html_content
