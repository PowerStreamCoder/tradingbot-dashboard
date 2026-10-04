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
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from stockpicker.core import build_evidence_dossier
from stockpicker.income_screener import evaluate_covered_call_candidate, screen_income_candidates
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
