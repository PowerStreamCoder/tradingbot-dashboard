"""
Unit tests for StockPicker Enhancement:
- State-aware subsequent runs (deduping, lead promotion, already accepted PROVISIONED status)
- Multi-dimensional Evidence Dossier (clickable SEC link, XAI thesis)
- Track 2 Covered Call Income Screener
- HITL API Endpoints (Accept, Reject with cooldown, Provision-bot)
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timezone, timedelta

from stockpicker.core import build_evidence_dossier, compute_fundamental_score
from stockpicker.income_screener import evaluate_covered_call_candidate, screen_income_candidates
from stockpicker.parameter_advisor import generate_expert_parameter_advice
from stockpicker.runner import hydrate_history_state, load_registered_bots, run_stockpicker


@patch("stockpicker.alternative_data_client.fetch_alternative_catalysts")
def test_build_evidence_dossier_growth(mock_alt):
    """Verify evidence dossier for Track 1 Growth candidate."""
    mock_alt.return_value = {
        "contracts": [{"award_amount": 95000000, "agency": "Department of Defense", "award_description": "Tactical drone"}],
        "total_contract_value": 95000000,
        "congress_trades": []
    }
    fundamentals = {
        "current_price": 28.50,
        "market_cap": 3500000000,
        "revenue_yoy": 0.24,
        "gross_margin": 0.42,
        "debt_to_equity": 65.0,
        "current_ratio": 1.85,
        "operating_margin": 0.14,
    }
    dossier = build_evidence_dossier(
        ticker="KTOS",
        industry="Aerospace & Defense",
        catalyst="Department of Defense awards $95M tactical drone contract",
        fundamentals=fundamentals,
        strategy_track="GROWTH"
    )

    assert "sec_filing_url" in dossier
    assert "edgar" in dossier["sec_filing_url"].lower()
    assert len(dossier["bull_drivers"]) >= 2
    assert len(dossier["risk_warnings"]) >= 1
    assert dossier["solvency_rating"] in ("Pristine", "Robust", "Adequate", "Distressed")
    assert any("Tactical drone" in b or "contract" in b.lower() for b in dossier["bull_drivers"])


@patch("stockpicker.alternative_data_client.fetch_alternative_catalysts")
def test_build_evidence_dossier_income(mock_alt):
    """Verify evidence dossier for Track 2 Income candidate with options metrics."""
    mock_alt.return_value = {"contracts": [], "congress_trades": []}
    options_data = {
        "ticker": "XLF",
        "current_price": 42.0,
        "strike": 43.0,
        "monthly_yield_est": 2.2,
        "annualized_yield_est": 26.4,
        "implied_volatility": 18.5,
        "bid_ask_spread": 0.02,
        "open_interest": 15000,
        "days_to_earnings": 45,
        "earnings_risk_flag": False
    }
    dossier = build_evidence_dossier(
        ticker="XLF",
        industry="Covered Call Income",
        catalyst="Low volatility sector ETF ideal for option overwrite",
        fundamentals={"current_price": 42.0},
        strategy_track="INCOME",
        options_data=options_data
    )

    assert dossier["options_yield_metrics"]["monthly_yield_est"] == 2.2
    assert dossier["options_yield_metrics"]["open_interest"] == 15000
    assert any("premium yield" in b.lower() or ("call" in b.lower() and "yield" in b.lower()) for b in dossier["bull_drivers"])


@patch("requests.get")
def test_evaluate_covered_call_candidate(mock_get):
    """Test Covered Call income scoring logic with mock Yahoo Finance responses."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "optionChain": {
            "result": [{
                "quote": {
                    "regularMarketPrice": 100.0,
                    "trailingAnnualDividendYield": 0.02
                },
                "options": [{
                    "expirationDate": 1775000000,
                    "calls": [{
                        "strike": 102.0,
                        "bid": 2.20,
                        "ask": 2.30,
                        "openInterest": 2500,
                        "impliedVolatility": 0.22
                    }]
                }]
            }]
        }
    }
    mock_get.return_value = mock_resp

    res = evaluate_covered_call_candidate("SPY")
    assert res is not None
    assert res["ticker"] == "SPY"
    assert res["current_price"] == 100.0
    assert res["strike"] == 102.0
    assert res["income_score"] > 50.0
    assert res["strategy_track"] == "INCOME"


def test_subsequent_run_state_awareness_and_promotion():
    """
    Test subsequent run behavior:
    1. KTOS was already accepted / provisioned -> Stamped PROVISIONED, placed in already_accepted.
    2. KTOS does NOT consume one of the 5 actionable lead slots.
    3. Fresh candidates (e.g. CRWD) are promoted into actionable leads.
    4. Rejected candidate within cooldown is suppressed.
    """
    mock_db = MagicMock()

    # Previous Firestore state: KTOS is provisioned, PLTR was rejected yesterday
    mock_doc = MagicMock()
    mock_doc.exists = True
    yesterday_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    mock_doc.to_dict.return_value = {
        "already_accepted": [
            {
                "ticker": "KTOS",
                "symbol": "KTOS",
                "status": "PROVISIONED",
                "bot_id": "ktos_sma",
                "client_id": 5,
                "score": 88.0,
                "provisioned_at": yesterday_iso
            }
        ],
        "picks": [],
        "rejected_cooldown": [
            {
                "ticker": "PLTR",
                "symbol": "PLTR",
                "status": "REJECTED",
                "score": 70.0,
                "rejected_at": yesterday_iso,
                "rejection_reason": "VALUATION_EXCESSIVE"
            }
        ]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    state = hydrate_history_state(mock_db)
    assert "KTOS" in state["accepted"]
    assert "PLTR" in state["rejected"]

    # Mock runner execution with candidates: KTOS (already accepted), PLTR (rejected), CRWD (fresh lead)
    with patch("stockpicker.runner.load_registered_bots", return_value={"KTOS": {"name": "ktos_sma", "client_id": 5}}), \
         patch("stockpicker.runner.hydrate_history_state", return_value=state), \
         patch("stockpicker.runner.fetch_all_news", return_value=[{"headline": "test", "news_source": "test"}]), \
         patch("stockpicker.runner.rank_news", return_value=[{"impacted_us_tickers": ["CRWD"], "explosiveness": 8.5}]), \
         patch("stockpicker.runner.score_candidates", return_value=[
             {"ticker": "KTOS", "composite_score": 89.5, "status": "PENDING_REVIEW"},
             {"ticker": "CRWD", "composite_score": 91.0, "status": "PENDING_REVIEW"},
             {"ticker": "PLTR", "composite_score": 72.0, "status": "PENDING_REVIEW"}
         ]), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        result = run_stockpicker()

        assert result is not None
        # 1. KTOS must be in already_accepted with PROVISIONED status
        accepted_tickers = [x["ticker"] for x in result["already_accepted"]]
        assert "KTOS" in accepted_tickers
        ktos_item = next(x for x in result["already_accepted"] if x["ticker"] == "KTOS")
        assert ktos_item["status"] == "PROVISIONED"
        assert ktos_item["bot_id"] == "ktos_sma"
        assert ktos_item["current_score"] == 89.5
        assert ktos_item["score_delta"] == "+1.5"

        # 2. KTOS must NOT be in actionable_leads
        actionable_tickers = [x["ticker"] for x in result["growth_picks"]]
        assert "KTOS" not in actionable_tickers

        # 3. CRWD must be promoted into actionable leads as a fresh lead
        assert "CRWD" in actionable_tickers
        crwd_item = next(x for x in result["growth_picks"] if x["ticker"] == "CRWD")
        assert crwd_item["status"] == "PENDING_REVIEW"
        assert crwd_item.get("is_new_lead") is True

        # 4. PLTR must be suppressed in rejected_cooldown
        rejected_tickers = [x["ticker"] for x in result["rejected_cooldown"]]
        assert "PLTR" in rejected_tickers
        assert "PLTR" not in actionable_tickers


def test_api_accept_and_provision_endpoints():
    """Verify accept, reject and provision API routes using FastAPI TestClient."""
    from fastapi.testclient import TestClient
    from main import app, authenticated_sessions, dashboard_data

    client = TestClient(app)
    authenticated_sessions["test-session-123"] = datetime.now()
    client.cookies.set("dashboard_session", "test-session-123")

    # Mock Firestore stock_picks document
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "picks": [{"ticker": "CRWD", "symbol": "CRWD", "status": "PENDING_REVIEW"}],
        "actionable_leads": [{"ticker": "CRWD", "symbol": "CRWD", "status": "PENDING_REVIEW"}],
        "growth_picks": [{"ticker": "CRWD", "symbol": "CRWD", "status": "PENDING_REVIEW"}],
        "already_accepted": [],
        "rejected_cooldown": []
    }
    mock_ref = MagicMock()
    mock_ref.document.return_value.get.return_value = mock_doc
    dashboard_data.stock_picks_ref = mock_ref

    # 1. Test Accept Lead
    resp_accept = client.post("/api/stock-picks/accept", json={
        "symbol": "CRWD",
        "strategy_track": "GROWTH",
        "notes": "Strong cybersecurity contract pipeline"
    })
    assert resp_accept.status_code == 200
    data_accept = resp_accept.json()
    assert data_accept["status"] == "success"
    assert data_accept["lead_status"] == "ACCEPTED"

    # 2. Test Reject Lead
    resp_reject = client.post("/api/stock-picks/reject", json={
        "symbol": "CRWD",
        "reason": "VALUATION_EXCESSIVE",
        "notes": "EV/Sales multiple too high"
    })
    assert resp_reject.status_code == 200
    data_reject = resp_reject.json()
    assert data_reject["status"] == "success"
    assert data_reject["lead_status"] == "REJECTED"
    assert "cooldown_expires" in data_reject


def test_generate_expert_parameter_advice_growth_and_income():
    """Verify expert parameter calculations across volatility regimes and tracks."""
    from stockpicker.parameter_advisor import generate_expert_parameter_advice

    # 1. High Volatility Growth Asset
    advice_growth = generate_expert_parameter_advice(
        symbol="KTOS",
        strategy_track="GROWTH",
        capital_allocation=10000.0,
        current_price=25.0,
        est_atr_pct=0.035,  # 3.5% ATR -> High volatility
        solvency_rating="Robust"
    )
    assert advice_growth["risk_tier"] == "High"
    params_g = advice_growth["recommended_params"]
    assert params_g["atr_parameters"]["trailing_stop_atr_multiplier"] == 2.8
    # $5,000 bucket / $25 price = 200 -> capped at 100
    assert params_g["atr_parameters"]["position_min_shares"] == 100
    assert params_g["covered_calls"]["cc_mode"] == "TOTAL_RETURN"
    assert params_g["covered_calls"]["call_expiration_days"] == 7
    assert len(advice_growth["expert_rationale"]) == 4

    # 2. Low Volatility Income Asset with High Share Price
    advice_income = generate_expert_parameter_advice(
        symbol="SPY",
        strategy_track="INCOME",
        capital_allocation=10000.0,
        current_price=500.0,
        est_atr_pct=0.010,  # 1.0% ATR -> Low volatility
        solvency_rating="Pristine"
    )
    assert advice_income["risk_tier"] == "Low"
    params_i = advice_income["recommended_params"]
    assert params_i["atr_parameters"]["trailing_stop_atr_multiplier"] == 1.6
    # $5,000 bucket / $500 price = 10 shares
    assert params_i["atr_parameters"]["position_min_shares"] == 10
    assert params_i["covered_calls"]["cc_mode"] == "INCOME"
    assert params_i["covered_calls"]["call_expiration_days"] == 30
    assert params_i["capital"]["capital_per_bucket_short"] == 0.0


def test_api_parameter_advice_and_provision_failure_rollback(tmp_path):
    """Verify GET parameter-advice endpoint and provisioning failure rollback with PROVISIONING_FAILED state."""
    from fastapi.testclient import TestClient
    from main import app, authenticated_sessions, dashboard_data

    client = TestClient(app)
    authenticated_sessions["test-session-adv"] = datetime.now() + timedelta(hours=1)
    client.cookies.set("dashboard_session", "test-session-adv")

    # Mock Firestore document
    mock_doc = MagicMock()
    mock_doc.exists = True
    doc_data = {
        "actionable_leads": [
            {
                "ticker": "FAILSYM",
                "symbol": "FAILSYM",
                "status": "PENDING_REVIEW",
                "current_price": 50.0,
                "est_atr_pct": 2.5
            }
        ],
        "growth_picks": [],
        "income_picks": [],
        "already_accepted": []
    }
    mock_doc.to_dict.return_value = doc_data
    mock_ref = MagicMock()
    mock_ref.document.return_value.get.return_value = mock_doc
    dashboard_data.stock_picks_ref = mock_ref

    # 1. Test GET /api/stock-picks/parameter-advice
    resp_adv = client.get("/api/stock-picks/parameter-advice?symbol=FAILSYM&strategy_track=GROWTH&capital_allocation=15000")
    assert resp_adv.status_code == 200
    adv_data = resp_adv.json()
    assert adv_data["symbol"] == "FAILSYM"
    assert "recommended_params" in adv_data
    assert "expert_rationale" in adv_data

    # 2. Test POST /api/stock-picks/provision-bot with failure during parameter write
    fake_bots_json = tmp_path / "bots.json"
    fake_bots_json.write_text(json.dumps({
        "bots": [{"name": "existing_bot", "symbol": "EXIST", "client_id": 3, "enabled": True}]
    }))

    # Patch os.makedirs to raise PermissionError when creating bots directory
    with patch("main._bot_config_cache", {"config": None, "mtime": None, "version": 0, "lock": MagicMock()}):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_bots_json.read_text())), \
             patch("os.makedirs", side_effect=PermissionError("Simulated disk write permission denied")):

            resp_fail = client.post("/api/stock-picks/provision-bot", json={
                "symbol": "FAILSYM",
                "strategy_track": "GROWTH",
                "capital_allocation": 10000
            })

            assert resp_fail.status_code == 500
            err_json = resp_fail.json()
            assert "Permission denied" in err_json["detail"] or "Simulated" in err_json["detail"]

            # Verify Firestore was updated with PROVISIONING_FAILED status
            mock_ref.document.return_value.set.assert_called()
            saved_data = mock_ref.document.return_value.set.call_args[0][0]
            lead_item = next(x for x in saved_data["actionable_leads"] if x["symbol"] == "FAILSYM")
            assert lead_item["status"] == "PROVISIONING_FAILED"
            assert "Permission denied" in lead_item["failure_reason"] or "Simulated" in lead_item["failure_reason"]
            assert lead_item["failure_stage"] == "WRITING_BOT_PARAMS"
            assert "suggested_action" in lead_item


@patch("stockpicker.alternative_data_client.fetch_alternative_catalysts", return_value={"contracts": [], "congress_trades": []})
@patch("stockpicker.core.get_companyfacts_quarterly")
@patch("stockpicker.core.get_yahoo_financial_snapshot")
@patch("stockpicker.core.get_alpha_earnings")
@patch("stockpicker.core.get_finnhub_insider_trades")
def test_data_source_degradation_informational_output(mock_finn, mock_av, mock_yf, mock_sec, mock_alt):
    """
    Verify that when one or all data sources are missing or fail:
    1. Output explicitly flags data_quality (PARTIAL / DEGRADED)
    2. Output lists specific missing_sources and degradation_warnings
    3. Evidence dossier synthesizes degradation warning into risk drivers
    """
    mock_finn.return_value = {"insider_score": 0, "reason": "no insider data"}
    mock_av.return_value = {}

    # Case 1: SEC EDGAR is down/empty, but Yahoo Finance succeeds
    mock_sec.return_value = {}
    mock_yf.return_value = {
        "price": {"regularMarketPrice": 45.0, "marketCap": 2000000000},
        "financialData": {"grossMargins": 0.35, "debtToEquity": 50.0, "currentRatio": 1.5}
    }

    fund_partial = compute_fundamental_score("TESTSYM")
    assert fund_partial["is_degraded"] is True
    assert fund_partial["data_quality"] == "PARTIAL"
    assert "SEC EDGAR" in fund_partial["missing_sources"]
    assert any("SEC quarterly filings unavailable" in w for w in fund_partial["degradation_warnings"])

    # Build dossier with partial data
    dossier = build_evidence_dossier(
        ticker="TESTSYM",
        industry="Defense",
        catalyst="Test catalyst",
        fundamentals=fund_partial,
        strategy_track="GROWTH"
    )
    assert dossier["is_degraded"] is True
    assert dossier["data_quality"] == "PARTIAL"
    assert "SEC EDGAR" in dossier["missing_sources"]
    assert any("Scoring Degraded" in r for r in dossier["risk_warnings"])

    # Case 2: All sources unavailable (SEC + Yahoo Finance empty)
    mock_sec.return_value = {}
    mock_yf.return_value = {}

    fund_degraded = compute_fundamental_score("FAILSYS")
    assert fund_degraded["is_degraded"] is True
    assert fund_degraded["data_quality"] == "DEGRADED"
    assert "SEC EDGAR" in fund_degraded["missing_sources"]
    assert "Yahoo Finance" in fund_degraded["missing_sources"]
    assert len(fund_degraded["degradation_warnings"]) >= 2


def test_parameter_advisor_handles_degraded_inputs():
    """Verify that the Quant Parameter Advisor flags degraded inputs to protect capital."""
    advice = generate_expert_parameter_advice(
        symbol="UNVERIFIED",
        strategy_track="GROWTH",
        capital_allocation=12000.0,
        current_price=50.0,
        est_atr_pct=0.012,
        solvency_rating="Pristine",
        is_degraded=True,
        missing_sources=["SEC EDGAR", "Alpha Vantage"]
    )

    assert advice["is_degraded"] is True
    assert "SEC EDGAR" in advice["missing_sources"]
    # Due to degraded inputs, Low risk is adjusted to Moderate
    assert advice["risk_tier"] == "Moderate"
    # Advisory rationale contains caution warning
    assert any("Data Degradation Alert" in r for r in advice["expert_rationale"])
    assert "inputs degraded" in advice["advisory_summary"]


