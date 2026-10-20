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


def test_parameter_advisor_3_modes():
    """
    Verify parameter advisor handles all 3 canonical modes:
    1. EQUITY: covered calls disabled (use_covered_calls=False, call_expiration_days=0)
    2. TOTAL_RETURN: covered calls enabled (use_covered_calls=True, call_expiration_days=7)
    3. INCOME: covered calls enabled (use_covered_calls=True, call_expiration_days=30)
    """
    from stockpicker.parameter_advisor import generate_expert_parameter_advice

    # 1. EQUITY mode
    adv_eq = generate_expert_parameter_advice(
        symbol="XLV",
        strategy_track="GROWTH",
        capital_allocation=10000.0,
        cc_mode="EQUITY"
    )
    cc_eq = adv_eq["recommended_params"]["covered_calls"]
    assert cc_eq["use_covered_calls"] is False
    assert cc_eq["cc_mode"] == "EQUITY"
    assert cc_eq["call_expiration_days"] == 0
    assert "pure Equity mode (covered calls disabled)" in adv_eq["advisory_summary"]

    # 2. TOTAL_RETURN mode
    adv_tr = generate_expert_parameter_advice(
        symbol="XLV",
        strategy_track="GROWTH",
        capital_allocation=10000.0,
        cc_mode="TOTAL_RETURN"
    )
    cc_tr = adv_tr["recommended_params"]["covered_calls"]
    assert cc_tr["use_covered_calls"] is True
    assert cc_tr["cc_mode"] == "TOTAL_RETURN"
    assert cc_tr["call_expiration_days"] == 7

    # 3. INCOME mode
    adv_inc = generate_expert_parameter_advice(
        symbol="XLV",
        strategy_track="INCOME",
        capital_allocation=10000.0,
        cc_mode="INCOME"
    )
    cc_inc = adv_inc["recommended_params"]["covered_calls"]
    assert cc_inc["use_covered_calls"] is True
    assert cc_inc["cc_mode"] == "INCOME"
    assert cc_inc["call_expiration_days"] == 30


def test_api_parameter_advice_cc_mode(auth_client):
    """
    Verify GET /api/stock-picks/parameter-advice supports ?cc_mode=EQUITY
    and returns use_covered_calls: False.
    """
    resp = auth_client.get("/api/stock-picks/parameter-advice?symbol=XLV&strategy_track=GROWTH&cc_mode=EQUITY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended_params"]["covered_calls"]["use_covered_calls"] is False
    assert data["recommended_params"]["covered_calls"]["cc_mode"] == "EQUITY"


def test_provision_bot_equity_mode_disables_covered_calls(auth_client, tmp_path):
    """
    Verify that provisioning a bot with cc_mode="EQUITY" writes a bot configuration
    with covered_calls.use_covered_calls = False, cc_mode = "EQUITY", and call_expiration_days = 0.
    """
    cand_data = {
        "ticker": "XLV",
        "symbol": "XLV",
        "composite_score": 85.0,
        "score": 85.0,
        "strategy_track": "GROWTH",
        "current_price": 140.0,
        "est_atr_pct": 1.8
    }

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "actionable_leads": [cand_data],
        "growth_picks": [cand_data],
        "already_accepted": []
    }

    written_files = {}
    orig_open = open

    def fake_open(file, mode="r", *args, **kwargs):
        if "w" in mode:
            m = mock_open()()
            written_files[str(file)] = m
            return m
        elif str(file).endswith("bots.json"):
            return mock_open(read_data=json.dumps({"bots": []}))()
        elif str(file).endswith("nvda.json"):
            return mock_open(read_data=json.dumps({
                "symbol": "NVDA",
                "covered_calls": {
                    "use_covered_calls": True,
                    "cc_mode": "TOTAL_RETURN",
                    "call_expiration_days": 7
                }
            }))()
        return orig_open(file, mode, *args, **kwargs)

    with patch("main.load_bot_configs"), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", side_effect=fake_open), \
         patch("os.makedirs"), \
         patch.object(dashboard_data.stock_picks_ref, "document") as mock_doc_ref, \
         patch("httpx.AsyncClient.post", return_value=MagicMock(status_code=200)):

        mock_doc_ref.return_value.get.return_value = mock_doc

        resp = auth_client.post("/api/stock-picks/provision-bot", json={
            "symbol": "XLV",
            "strategy_track": "GROWTH",
            "capital_allocation": 10000.0,
            "custom_params": {
                "cc_mode": "EQUITY",
                "trailing_stop_atr_multiplier": 2.2,
                "position_min_shares": 35
            }
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["symbol"] == "XLV"

        # Check written bot configuration
        bot_file = next(f for f in written_files if f.endswith("xlv.json"))
        written_chunks = [call.args[0] for call in written_files[bot_file].write.call_args_list]
        bot_cfg = json.loads("".join(written_chunks))

        assert bot_cfg["covered_calls"]["use_covered_calls"] is False
        assert bot_cfg["covered_calls"]["cc_mode"] == "EQUITY"
        assert bot_cfg["covered_calls"]["call_expiration_days"] == 0
        assert bot_cfg["atr_parameters"]["trailing_stop_atr_multiplier"] == 2.2
        assert bot_cfg["atr_parameters"]["position_min_shares"] == 35


def test_frontend_modal_ux_and_custom_dropdown_contract():
    """
    Contract test: verifies that:
    1. bot_focus.html contains the 3 options (EQUITY, TOTAL_RETURN, INCOME), modal close buttons, and custom select
    2. stock_picker.js implements and exports toggleCcModeDropdown, selectCcMode, and applyModeUiState
    """
    html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "bot_focus.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert 'class="sp-modal-overlay"' in html
    assert 'class="sp-modal-close-btn"' in html
    assert 'data-value="EQUITY"' in html
    assert 'data-value="TOTAL_RETURN"' in html
    assert 'data-value="INCOME"' in html
    assert 'id="modal-call-exp-container"' in html
    assert 'id="modal-call-exp-notice"' in html

    js_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "stock_picker.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    assert "toggleCcModeDropdown" in js
    assert "selectCcMode" in js
    assert "applyModeUiState" in js
    assert "window.toggleCcModeDropdown = toggleCcModeDropdown" in js
    assert "window.selectCcMode = selectCcMode" in js
    assert "disabled-field" in js


def test_stockpicker_js_track_and_status_defined():
    """
    Regression test: Ensure stock_picker.js defines track and status variables
    in renderStockPicksTable to avoid 'track is not defined' ReferenceError.
    """
    js_path = os.path.join(os.path.dirname(__file__), "..", "static", "js", "stock_picker.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    assert "const track = safeTrack(pick.strategy_track);" in js
    assert "const status = pick.status || 'PENDING_REVIEW';" in js


def test_load_bot_configs_merges_provisioned_bots_from_firestore(tmp_path):
    """
    Verify that load_bot_configs() merges bots from Firestore stock_picks/current already_accepted
    that are marked PROVISIONED even if bots.json does not yet list them (e.g., after redeployment).
    """
    from main import load_bot_configs, _bot_config_cache

    fake_config = {
        "bots": [
            {"name": "qqq_sma", "symbol": "QQQ", "client_id": 7, "enabled": True}
        ]
    }
    config_file = tmp_path / "bots.json"
    config_file.write_text(json.dumps(fake_config))

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "already_accepted": [
            {
                "symbol": "AFRM",
                "client_id": 8,
                "status": "PROVISIONED",
                "strategy_track": "GROWTH",
                "composite_score": 80.74,
                "strategy": "sma_crossover"
            }
        ]
    }

    mock_stock_ref = MagicMock()
    mock_stock_ref.document.return_value.get.return_value = mock_doc

    with _bot_config_cache['lock']:
        _bot_config_cache['config'] = None
        _bot_config_cache['mtime'] = None

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(fake_config))), \
         patch.object(dashboard_data, "stock_picks_ref", mock_stock_ref):
        cfg = load_bot_configs()

        bot_symbols = [b["symbol"] for b in cfg.get("bots", [])]
        assert "QQQ" in bot_symbols
        assert "AFRM" in bot_symbols
        afrm = next(b for b in cfg["bots"] if b["symbol"] == "AFRM")
        assert afrm["client_id"] == 8
        assert afrm["strategy_track"] == "GROWTH"


def test_load_bot_parameters_falls_back_to_firestore(tmp_path):
    """
    Verify that load_bot_parameters falls back to Firestore 'bots' collection
    when the local json file is not found on disk.
    """
    from main import load_bot_parameters, _bot_params_cache

    with _bot_params_cache["lock"]:
        _bot_params_cache["params"].pop("AFRM", None)
        _bot_params_cache["mtimes"].pop("AFRM", None)

    mock_doc = MagicMock()
    mock_doc.to_dict.return_value = {
        "symbol": "AFRM",
        "bot_params": {
            "symbol": "AFRM",
            "capital": {"capital_per_bucket_long": 5000.0},
            "covered_calls": {"cc_mode": "EQUITY"}
        }
    }

    mock_db = MagicMock()
    mock_coll = MagicMock()
    mock_query = MagicMock()
    mock_limit = MagicMock()
    mock_limit.stream.return_value = [mock_doc]
    mock_query.limit.return_value = mock_limit
    mock_coll.where.return_value = mock_query
    mock_db.collection.return_value = mock_coll

    with patch("os.path.exists", return_value=False), \
         patch.object(dashboard_data, "db", mock_db):
        params = load_bot_parameters("AFRM")
        assert params is not None
        assert params["symbol"] == "AFRM"
        assert params["covered_calls"]["cc_mode"] == "EQUITY"


