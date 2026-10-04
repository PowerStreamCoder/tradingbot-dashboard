"""
Edge Case Test Suite for StockPicker Enhancement.

Covers boundary conditions, missing/corrupted data, network failures,
security validations, and edge-of-envelope scenarios across:
1. income_screener.py (Track 2 Covered Call Screener)
2. alternative_data_client.py (Zero-Cost Public Data Connectors)
3. core.py (build_evidence_dossier & Solvency Ratings)
4. runner.py (State Hydration, Deduping & Lead Promotion Boundaries)
5. main.py (HITL Endpoints & Security Sanitization)
"""

import json
import pytest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from stockpicker.income_screener import (
    evaluate_covered_call_candidate,
    screen_income_candidates,
    fetch_yahoo_options,
    fetch_earnings_date
)
from stockpicker.alternative_data_client import (
    fetch_usaspending_contracts,
    fetch_congressional_trades,
    fetch_alternative_catalysts,
    _congressional_cache
)
from stockpicker.core import build_evidence_dossier
from stockpicker.runner import (
    load_registered_bots,
    hydrate_history_state,
    run_stockpicker
)
from main import app, authenticated_sessions, dashboard_data


# =============================================================================
# 1. INCOME SCREENER EDGE CASES
# =============================================================================

@patch("requests.get")
def test_income_screener_empty_and_malformed_options_chain(mock_get):
    """Test options screener resilience against empty/malformed Yahoo API responses."""
    # Subcase A: Non-200 status code
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_get.return_value = mock_resp
    assert fetch_yahoo_options("INVALID") is None
    assert evaluate_covered_call_candidate("INVALID") is None

    # Subcase B: Empty optionChain result array
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"optionChain": {"result": []}}
    assert evaluate_covered_call_candidate("EMPTY") is None

    # Subcase C: regularMarketPrice is 0 or negative
    mock_resp.json.return_value = {
        "optionChain": {
            "result": [{
                "quote": {"regularMarketPrice": 0.0},
                "options": [{"calls": [{"strike": 100, "bid": 2.0}]}]
            }]
        }
    }
    assert evaluate_covered_call_candidate("ZERO_PRICE") is None

    # Subcase D: No calls array in options
    mock_resp.json.return_value = {
        "optionChain": {
            "result": [{
                "quote": {"regularMarketPrice": 100.0},
                "options": [{}]
            }]
        }
    }
    assert evaluate_covered_call_candidate("NO_CALLS") is None


@patch("requests.get")
def test_income_screener_zero_bid_and_extreme_spread(mock_get):
    """Test options screener when all call options have 0 bid (illiquid)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "optionChain": {
            "result": [{
                "quote": {"regularMarketPrice": 50.0},
                "options": [{
                    "calls": [
                        {"strike": 52.0, "bid": 0.0, "ask": 5.0, "openInterest": 0}
                    ]
                }]
            }]
        }
    }
    mock_get.return_value = mock_resp
    res = evaluate_covered_call_candidate("ILLIQUID")
    assert res is not None
    # 0 bid means monthly yield is 0.0
    assert res["monthly_yield_est"] == 0.0
    # Illiquid option should have penalized score
    assert res["income_score"] < 50.0


@patch("requests.get")
def test_income_screener_earnings_lockout_boundary(mock_get):
    """Test earnings lockout penalty boundary (35 days vs 36 days vs 25 days)."""
    # Helper to generate mock response with specific earnings date
    def generate_mock(days_away: int):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        earnings_ts = int((datetime.now(timezone.utc) + timedelta(days=days_away)).timestamp())
        mock_resp.json.return_value = {
            "optionChain": {
                "result": [{
                    "quote": {"regularMarketPrice": 100.0},
                    "options": [{
                        "calls": [{"strike": 102.0, "bid": 2.50, "ask": 2.60, "openInterest": 5000, "impliedVolatility": 0.20}]
                    }]
                }]
            },
            "quoteSummary": {
                "result": [{
                    "calendarEvents": {
                        "earnings": {"earningsDate": [{"raw": earnings_ts}]}
                    }
                }]
            }
        }
        return mock_resp

    # Within dangerous lockout window (20 days) -> penalty flag applied
    mock_get.return_value = generate_mock(20)
    res_danger = evaluate_covered_call_candidate("DANGER")
    assert res_danger is not None
    assert res_danger["earnings_risk_flag"] is True

    # Far away (>35 days) -> bonus applied
    mock_get.return_value = generate_mock(45)
    res_safe = evaluate_covered_call_candidate("SAFE")
    assert res_safe is not None
    assert res_safe["earnings_risk_flag"] is False
    assert res_safe["income_score"] > res_danger["income_score"]


# =============================================================================
# 2. ALTERNATIVE DATA CLIENT EDGE CASES
# =============================================================================

@patch("requests.post")
def test_usaspending_network_failure_and_timeout(mock_post):
    """Verify USAspending connector handles HTTP errors and timeouts gracefully."""
    mock_post.side_effect = Exception("Connection timed out")
    res = fetch_usaspending_contracts("LMT")
    assert res == []


@patch("requests.get")
def test_stockwatcher_cache_and_corrupt_data(mock_get):
    """Test StockWatcher S3 cache hit logic and handling of corrupt JSON."""
    _congressional_cache["data"] = None
    _congressional_cache["timestamp"] = None

    # Corrupted response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    mock_get.return_value = mock_resp

    trades = fetch_congressional_trades("NVDA")
    assert trades == []

    # Valid response cached
    mock_resp.json.side_effect = None
    mock_resp.json.return_value = [
        {"ticker": "NVDA", "representative": "Nancy Pelosi", "type": "purchase", "amount": "$1,000,001 - $5,000,000", "transaction_date": datetime.now().strftime("%Y-%m-%d")}
    ]
    trades_valid = fetch_congressional_trades("NVDA")
    assert len(trades_valid) == 1
    assert trades_valid[0]["representative"] == "Nancy Pelosi"

    # Subsequent call must hit in-memory cache without calling requests.get again
    mock_get.reset_mock()
    trades_cached = fetch_congressional_trades("NVDA")
    assert len(trades_cached) == 1
    mock_get.assert_not_called()


# =============================================================================
# 3. EVIDENCE DOSSIER & SOLVENCY BOUNDARY CONDITIONS
# =============================================================================

@patch("stockpicker.alternative_data_client.fetch_alternative_catalysts")
def test_solvency_boundary_conditions(mock_alt):
    """Verify exact numerical boundaries for capital structure solvency ratings."""
    mock_alt.return_value = {"contracts": [], "congress_trades": []}

    # Pristine: CR >= 1.5 AND DE < 80
    dossier_pristine = build_evidence_dossier("TEST", "Tech", "", {"current_ratio": 1.50, "debt_to_equity": 79.9})
    assert dossier_pristine["solvency_rating"] == "Pristine"

    # Robust: CR >= 1.2 AND DE < 150 (CR below 1.5, or DE between 80 and 150)
    dossier_robust = build_evidence_dossier("TEST", "Tech", "", {"current_ratio": 1.49, "debt_to_equity": 79.9})
    assert dossier_robust["solvency_rating"] == "Robust"

    dossier_robust_de = build_evidence_dossier("TEST", "Tech", "", {"current_ratio": 1.80, "debt_to_equity": 120.0})
    assert dossier_robust_de["solvency_rating"] == "Robust"

    # Adequate: CR between 1.0 and 1.2, or DE between 150 and 250
    dossier_adequate = build_evidence_dossier("TEST", "Tech", "", {"current_ratio": 1.15, "debt_to_equity": 140.0})
    assert dossier_adequate["solvency_rating"] == "Adequate"

    # Distressed: CR < 1.0 OR DE > 250
    dossier_distressed_cr = build_evidence_dossier("TEST", "Tech", "", {"current_ratio": 0.95, "debt_to_equity": 50.0})
    assert dossier_distressed_cr["solvency_rating"] == "Distressed"

    dossier_distressed_de = build_evidence_dossier("TEST", "Tech", "", {"current_ratio": 1.80, "debt_to_equity": 260.0})
    assert dossier_distressed_de["solvency_rating"] == "Distressed"

    # Missing fundamentals -> Fallback to Adequate
    dossier_none = build_evidence_dossier("TEST", "Tech", "", {})
    assert dossier_none["solvency_rating"] == "Adequate"


# =============================================================================
# 4. RUNNER STATE AWARENESS, DEDUPING & BREAKTHROUGH OVERRIDE
# =============================================================================

def test_runner_breakthrough_override_boundary():
    """
    Test exact threshold for 7-day rejection breakthrough override:
    - Score jump Delta S < +20.0 (e.g. +19.9) -> Suppressed in cooldown.
    - Score jump Delta S >= +20.0 (e.g. +20.0) -> Breakthrough promoted into leads!
    """
    mock_db = MagicMock()
    two_days_ago_iso = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "already_accepted": [],
        "picks": [],
        "rejected_cooldown": [
            {
                "ticker": "SUB_THRESHOLD",
                "symbol": "SUB_THRESHOLD",
                "status": "REJECTED",
                "score": 60.0,
                "rejected_at": two_days_ago_iso,
                "rejection_reason": "VALUATION_EXCESSIVE"
            },
            {
                "ticker": "BREAKTHROUGH",
                "symbol": "BREAKTHROUGH",
                "status": "REJECTED",
                "score": 60.0,
                "rejected_at": two_days_ago_iso,
                "rejection_reason": "VALUATION_EXCESSIVE"
            }
        ]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    state = hydrate_history_state(mock_db)

    # SUB_THRESHOLD jumps from 60.0 to 79.9 (+19.9 delta -> Fail override)
    # BREAKTHROUGH jumps from 60.0 to 80.0 (+20.0 delta -> Success override)
    growth_candidates = [
        {"ticker": "SUB_THRESHOLD", "composite_score": 79.9, "status": "PENDING_REVIEW"},
        {"ticker": "BREAKTHROUGH", "composite_score": 80.0, "status": "PENDING_REVIEW"}
    ]

    with patch("stockpicker.runner.load_registered_bots", return_value={}), \
         patch("stockpicker.runner.hydrate_history_state", return_value=state), \
         patch("stockpicker.runner.fetch_all_news", return_value=[{"headline": "test", "news_source": "test"}]), \
         patch("stockpicker.runner.rank_news", return_value=[{"impacted_us_tickers": ["SUB_THRESHOLD", "BREAKTHROUGH"], "explosiveness": 8.0}]), \
         patch("stockpicker.runner.score_candidates", return_value=growth_candidates), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        result = run_stockpicker()

        # SUB_THRESHOLD should remain in rejected_cooldown
        rej_tickers = [x["ticker"] for x in result["rejected_cooldown"]]
        assert "SUB_THRESHOLD" in rej_tickers

        # BREAKTHROUGH should break out of cooldown and be promoted to actionable growth_picks
        promoted_tickers = [x["ticker"] for x in result["growth_picks"]]
        assert "BREAKTHROUGH" in promoted_tickers


def test_runner_deduping_across_dual_tracks():
    """Verify ticker appearing in both Growth and Income tracks is deduped."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    growth = [{"ticker": "DUAL_SYM", "composite_score": 85.0, "status": "PENDING_REVIEW"}]
    income = [{"ticker": "DUAL_SYM", "income_score": 78.0, "status": "PENDING_REVIEW"}]

    with patch("stockpicker.runner.load_registered_bots", return_value={}), \
         patch("stockpicker.runner.hydrate_history_state", return_value={"accepted": {}, "rejected": {}}), \
         patch("stockpicker.runner.fetch_all_news", return_value=[{"headline": "test"}]), \
         patch("stockpicker.runner.rank_news", return_value=[{"headline": "test", "explosiveness": 8.0}]), \
         patch("stockpicker.runner.score_candidates", return_value=growth), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=income), \
         patch("stockpicker.core.build_evidence_dossier", return_value={"solvency_rating": "Robust"}), \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        result = run_stockpicker()

        # DUAL_SYM should appear in growth_picks (first encountered), but NOT duplicate in income_picks
        growth_syms = [x["ticker"] for x in result["growth_picks"]]
        income_syms = [x["ticker"] for x in result["income_picks"]]
        assert "DUAL_SYM" in growth_syms
        assert "DUAL_SYM" not in income_syms


# =============================================================================
# 5. SECURITY SANITIZATION & HITL API ROBUSTNESS
# =============================================================================

def test_api_security_symbol_sanitization():
    """Verify API rejects path traversal and malicious symbols."""
    client = TestClient(app)
    authenticated_sessions["edge-session-456"] = datetime.now()
    client.cookies.set("dashboard_session", "edge-session-456")

    malicious_symbols = [
        "../../etc/passwd",
        "KTOS; DROP TABLE",
        "<script>alert(1)</script>",
        "AAPL/USD",
        "TOOLONGSYMBOL12345",
        "   "
    ]

    for sym in malicious_symbols:
        resp = client.post("/api/stock-picks/provision-bot", json={
            "symbol": sym,
            "strategy_track": "GROWTH"
        })
        assert resp.status_code == 400
        assert "Invalid ticker symbol" in resp.json()["detail"]


def test_api_provision_idempotency_when_already_exists(tmp_path):
    """Verify provision endpoint returns 200 with already_exists status if bot is configured."""
    client = TestClient(app)
    authenticated_sessions["edge-session-456"] = datetime.now()
    client.cookies.set("dashboard_session", "edge-session-456")

    # Mock bots.json containing NVDA
    fake_bots_json = tmp_path / "bots.json"
    fake_bots_json.write_text(json.dumps({
        "bots": [{"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "enabled": True}]
    }))

    with patch("main._bot_config_cache", {"config": None, "mtime": None, "version": 0, "lock": MagicMock()}):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_bots_json.read_text())):
            resp = client.post("/api/stock-picks/provision-bot", json={
                "symbol": "NVDA",
                "strategy_track": "GROWTH"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "already_exists"
            assert data["bot_id"] == "nvda_sma"
