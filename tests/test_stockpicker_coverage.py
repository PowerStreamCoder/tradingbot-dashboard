"""
High-Coverage Test Suite for StockPicker Enhancement Feature.

Targets 98%+ line and branch coverage across:
1. stockpicker/income_screener.py
2. stockpicker/alternative_data_client.py
3. stockpicker/runner.py
4. stockpicker/core.py (Dossier, XAI, Solvency, Candidate Scoring)
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timezone, timedelta

import requests

from stockpicker.income_screener import (
    fetch_yahoo_options,
    fetch_earnings_date,
    evaluate_covered_call_candidate,
    screen_income_candidates,
    INCOME_UNIVERSE
)
from stockpicker.alternative_data_client import (
    fetch_usaspending_contracts,
    fetch_congressional_trades,
    fetch_quiver_quant_data,
    fetch_alternative_catalysts,
    _congressional_cache
)
from stockpicker.core import (
    build_evidence_dossier,
    score_candidates
)
from stockpicker.runner import (
    setup_logging,
    detect_active_sources,
    write_empty_result,
    write_error_result,
    load_registered_bots,
    hydrate_history_state,
    run_stockpicker
)


# =============================================================================
# 1. stockpicker/alternative_data_client.py (Target: 100% Coverage)
# =============================================================================

def test_usaspending_contracts_empty_and_valid():
    """Verify USAspending query with empty input and with successful response."""
    # Empty recipient name returns []
    assert fetch_usaspending_contracts("") == []
    assert fetch_usaspending_contracts(None) == []

    # Successful API call
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "results": [
            {
                "Award ID": "CONT-101",
                "Recipient Name": "Palantir Tech",
                "Award Amount": 5000000.0,
                "Awarding Agency": "Department of Defense",
                "Start Date": "2026-01-15",
                "Description": "Data Platform Expansion"
            },
            {
                # Missing optional fields to test defaults (empty dict)
            }
        ]
    }

    with patch("requests.post", return_value=fake_response):
        contracts = fetch_usaspending_contracts("Palantir", days=90)
        assert len(contracts) == 2
        assert contracts[0]["award_id"] == "CONT-101"
        assert contracts[0]["amount"] == 5000000.0
        assert contracts[0]["agency"] == "Department of Defense"
        # Defaults
        assert contracts[1]["award_id"] == "N/A"
        assert contracts[1]["recipient"] == "Palantir"
        assert contracts[1]["amount"] == 0.0
        assert contracts[1]["agency"] == "Federal Agency"


def test_congressional_trades_filter_and_matching():
    """Verify congressional trades ticker matching, date cutoff, and transaction types."""
    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=200)).strftime("%Y-%m-%d")
    recent_date = (now - timedelta(days=5)).strftime("%Y-%m-%d")

    fake_txs = [
        {"ticker": "NVDA", "transaction_date": recent_date, "type": "Purchase", "representative": "Rep Alice", "amount": "$15k"},
        {"ticker": "NVDA", "transaction_date": recent_date, "type": "Buy", "representative": "Rep Bob"},
        {"ticker": "NVDA", "transaction_date": recent_date, "type": "Sale", "representative": "Rep Carol"},  # Should be skipped (not buy)
        {"ticker": "NVDA", "transaction_date": old_date, "type": "Purchase", "representative": "Rep Dave"},  # Should be skipped (too old)
        {"ticker": "AAPL", "transaction_date": recent_date, "type": "Purchase", "representative": "Rep Eve"},  # Different ticker
    ]

    _congressional_cache["timestamp"] = now
    _congressional_cache["data"] = fake_txs

    results = fetch_congressional_trades("nvda", days=60)
    assert len(results) == 2
    assert results[0]["representative"] == "Rep Alice"
    assert results[0]["type"] == "purchase"
    assert results[1]["representative"] == "Rep Bob"


def test_quiver_quant_data_coverage(monkeypatch):
    """Verify QuiverQuant data fetch under no-key, success, non-200, and exception."""
    # Case 1: No API key
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    assert fetch_quiver_quant_data("AAPL") is None

    # Case 2: API key present, HTTP 200
    monkeypatch.setenv("QUIVER_API_KEY", "test-key-123")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"AwardId": "Q-1", "Amount": 100000, "Agency": "NASA"}]

    with patch("requests.get", return_value=mock_resp):
        res = fetch_quiver_quant_data("AAPL")
        assert res is not None
        assert "gov_contracts" in res
        assert len(res["gov_contracts"]) == 1

    # Case 3: HTTP 403 / 500
    mock_resp.status_code = 403
    with patch("requests.get", return_value=mock_resp):
        assert fetch_quiver_quant_data("AAPL") is None

    # Case 4: Exception raised
    with patch("requests.get", side_effect=requests.RequestException("Network down")):
        assert fetch_quiver_quant_data("AAPL") is None


def test_fetch_alternative_catalysts_quiver_enrichment(monkeypatch):
    """Verify QuiverQuant fallback enrichment when USAspending returns empty."""
    monkeypatch.setenv("QUIVER_API_KEY", "fake-key")

    with patch("stockpicker.alternative_data_client.fetch_usaspending_contracts", return_value=[]), \
         patch("stockpicker.alternative_data_client.fetch_congressional_trades", return_value=[]), \
         patch("stockpicker.alternative_data_client.fetch_quiver_quant_data", return_value={
             "gov_contracts": [
                 {"AwardId": "QUIV-99", "Amount": 2500000, "Agency": "US Army", "Date": "2026-02-01", "Description": "Cyber Defense"}
             ]
         }):
        res = fetch_alternative_catalysts("KTOS", company_name="Kratos Defense")
        assert res["ticker"] == "KTOS"
        assert res["contract_count"] == 1
        assert res["total_contract_value"] == 2500000
        assert res["contracts"][0]["award_id"] == "QUIV-99"
        assert res["has_alternative_catalyst"] is True


# =============================================================================
# 2. stockpicker/income_screener.py (Target: 98%+ Coverage)
# =============================================================================

def test_fetch_yahoo_options_exception_handling():
    """Verify fetch_yahoo_options exception handling returns None."""
    with patch("requests.get", side_effect=requests.ConnectionError("Connection refused")):
        assert fetch_yahoo_options("ERR_SYM") is None


def test_fetch_earnings_date_exception_handling():
    """Verify fetch_earnings_date exception handling returns None."""
    with patch("requests.get", side_effect=requests.Timeout("Timeout")):
        assert fetch_earnings_date("ERR_SYM") is None


def test_evaluate_covered_call_candidate_missing_options_or_calls():
    """Verify evaluate_covered_call_candidate returns None when options or calls are empty."""
    # Quote present, but options empty list
    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value={
        "quote": {"regularMarketPrice": 100.0},
        "options": []
    }):
        assert evaluate_covered_call_candidate("NO_OPT") is None

    # Options present, but calls empty list
    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value={
        "quote": {"regularMarketPrice": 100.0},
        "options": [{"calls": []}]
    }):
        assert evaluate_covered_call_candidate("NO_CALLS") is None


def test_evaluate_covered_call_scoring_branches():
    """Test all yield, ATR, and liquidity scoring branches in evaluate_covered_call_candidate."""
    # Branch A: Moderate monthly yield (0.008 to 0.015), moderate open interest (100 to 500)
    fake_option_a = {
        "quote": {"regularMarketPrice": 100.0, "fiftyTwoWeekHigh": 110.0, "fiftyTwoWeekLow": 90.0},
        "options": [{
            "calls": [{
                "strike": 103.0,
                "bid": 1.0,  # 1.0 / 100 = 1.0% yield (0.010)
                "ask": 1.10,
                "openInterest": 250,
                "impliedVolatility": 0.25
            }]
        }]
    }

    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=fake_option_a), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(timezone.utc) + timedelta(days=60)):
        res_a = evaluate_covered_call_candidate("MOD_SYM")
        assert res_a is not None
        assert 0.008 <= (res_a["monthly_yield_est"] / 100.0) < 0.015
        assert res_a["open_interest"] == 250

    # Branch B: Very high monthly yield (> 0.040)
    fake_option_b = {
        "quote": {"regularMarketPrice": 100.0, "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 80.0},
        "options": [{
            "calls": [{
                "strike": 102.0,
                "bid": 5.0,  # 5.0 / 100 = 5.0% yield (0.050)
                "ask": 5.20,
                "openInterest": 1500,
                "impliedVolatility": 0.65
            }]
        }]
    }
    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=fake_option_b), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=None):
        res_b = evaluate_covered_call_candidate("HIGH_YIELD")
        assert res_b is not None
        assert res_b["monthly_yield_est"] > 4.0


def test_screen_income_candidates_universe_and_sorting():
    """Verify screen_income_candidates default universe and top_n ranking."""
    def fake_eval(sym):
        scores = {"AAPL": 85.0, "MSFT": 90.0, "AMD": 75.0}
        return {
            "ticker": sym,
            "income_score": scores.get(sym, 60.0),
            "current_price": 100.0
        }

    with patch("stockpicker.income_screener.evaluate_covered_call_candidate", side_effect=fake_eval):
        # Explicit universe
        top = screen_income_candidates(tickers=["AAPL", "MSFT", "AMD"], top_n=2)
        assert len(top) == 2
        assert top[0]["ticker"] == "MSFT"
        assert top[1]["ticker"] == "AAPL"

        # Default universe (tickers=None)
        all_top = screen_income_candidates(tickers=None, top_n=3)
        assert len(all_top) == 3


# =============================================================================
# 3. stockpicker/runner.py (Target: 98%+ Coverage)
# =============================================================================

def test_setup_logging_exception_handling():
    """Verify setup_logging gracefully handles failure to create log dir."""
    with patch("os.makedirs", side_effect=PermissionError("Permission denied")), \
         patch("builtins.print") as mock_print:
        setup_logging()
        mock_print.assert_called()


def test_detect_active_sources_all_envs(monkeypatch):
    """Verify detect_active_sources includes all configured env keys."""
    monkeypatch.setenv("NEWSAPI_KEY", "news-123")
    monkeypatch.setenv("X_BEARER_TOKEN", "twitter-token")
    monkeypatch.setenv("POLYGON_API_KEY", "poly-123")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-123")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "alpha-123")

    sources = detect_active_sources()
    assert "sec_edgar" in sources
    assert "yahoo_finance" in sources
    assert "newsapi" in sources
    assert "twitter" in sources
    assert "polygon" in sources
    assert "gemini" in sources
    assert "alphavantage" in sources


def test_write_empty_and_error_result_coverage():
    """Verify write_empty_result and write_error_result execution & failure handling."""
    mock_db = MagicMock()

    # Success paths
    write_empty_result(mock_db, "No news today", status="no_news")
    mock_db.collection.return_value.document.return_value.set.assert_called()

    write_error_result(mock_db, ValueError("Simulated DB failure"))
    mock_db.collection.return_value.document.return_value.set.assert_called()

    # Exception paths
    failing_db = MagicMock()
    failing_db.collection.side_effect = RuntimeError("Firestore down")
    # Should not raise uncaught exception
    write_empty_result(failing_db, "No news")
    write_error_result(failing_db, RuntimeError("Fatal"))


def test_load_registered_bots_various_paths(tmp_path):
    """Verify load_registered_bots finds config and handles JSON errors."""
    # Test valid bots.json
    cfg_dir = tmp_path / "tradingbot-config"
    cfg_dir.mkdir()
    bots_file = cfg_dir / "bots.json"
    bots_file.write_text(json.dumps({
        "bots": [
            {"symbol": "TSLA", "name": "tsla_bot"},
            {"symbol": "AAPL", "name": "aapl_bot"},
            {"name": "no_symbol_bot"}  # Should be ignored
        ]
    }))

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=bots_file.read_text())):
        bots = load_registered_bots()
        assert "TSLA" in bots
        assert "AAPL" in bots
        assert len(bots) == 2

    # Test corrupted bots.json
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="{invalid_json")):
        assert load_registered_bots() == {}

    # Test no config files found
    with patch("os.path.exists", return_value=False):
        assert load_registered_bots() == {}


def test_hydrate_history_state_none_and_exceptions():
    """Verify hydrate_history_state when db is None or raises an error."""
    # db is None
    res = hydrate_history_state(None)
    assert res == {"accepted": {}, "rejected": {}}

    # db raises exception
    failing_db = MagicMock()
    failing_db.collection.side_effect = Exception("Network timeout")
    res_err = hydrate_history_state(failing_db)
    assert res_err == {"accepted": {}, "rejected": {}}


def test_runner_corrupted_iso_rejection_date():
    """Verify rejection date with corrupted ISO string defaults to in_cooldown."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    growth = [{"ticker": "BAD_ISO", "composite_score": 60.0, "status": "PENDING_REVIEW"}]
    history = {
        "accepted": {},
        "rejected": {
            "BAD_ISO": {"score": 50.0, "rejected_at": "CORRUPTED_DATE_FORMAT"}
        }
    }

    with patch("stockpicker.runner.load_registered_bots", return_value={}), \
         patch("stockpicker.runner.hydrate_history_state", return_value=history), \
         patch("stockpicker.runner.fetch_all_news", return_value=[{"headline": "test"}]), \
         patch("stockpicker.runner.rank_news", return_value=[{"headline": "test", "explosiveness": 8.0}]), \
         patch("stockpicker.runner.score_candidates", return_value=growth), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        result = run_stockpicker()
        # Because score jump (60 - 50 = 10) < 20 and corrupted date keeps is_in_cooldown=True, it goes to rejected_cooldown
        assert len(result["rejected_cooldown"]) == 1
        assert result["rejected_cooldown"][0]["ticker"] == "BAD_ISO"


def test_runner_daily_archive_exception_handled():
    """Verify runner completes even if daily archive write to Firestore raises."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    
    # 'current' succeeds, but archive doc set raises Exception
    stock_picks_ref = MagicMock()
    doc_current = MagicMock()
    doc_archive = MagicMock()
    doc_archive.set.side_effect = Exception("Archive storage quota exceeded")

    def doc_side_effect(name):
        if name == "current":
            return doc_current
        return doc_archive

    stock_picks_ref.document.side_effect = doc_side_effect
    mock_db.collection.return_value = stock_picks_ref

    with patch("stockpicker.runner.load_registered_bots", return_value={}), \
         patch("stockpicker.runner.hydrate_history_state", return_value={"accepted": {}, "rejected": {}}), \
         patch("stockpicker.runner.fetch_all_news", return_value=[{"headline": "test"}]), \
         patch("stockpicker.runner.rank_news", return_value=[{"headline": "test", "explosiveness": 8.0}]), \
         patch("stockpicker.runner.score_candidates", return_value=[{"ticker": "ABC", "composite_score": 80.0, "status": "PENDING_REVIEW"}]), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        res = run_stockpicker()
        assert res["status"] == "success"
        doc_current.set.assert_called()


def test_runner_top_level_exception_and_error_write():
    """Verify run_stockpicker top-level exception calls write_error_result and re-raises."""
    mock_db = MagicMock()

    with patch("stockpicker.runner.load_registered_bots", side_effect=RuntimeError("Unrecoverable config failure")), \
         patch("stockpicker.runner.write_error_result") as mock_write_err, \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        with pytest.raises(RuntimeError, match="Unrecoverable config failure"):
            run_stockpicker()

        mock_write_err.assert_called_once()


# =============================================================================
# 4. stockpicker/core.py (Dossier, XAI, Solvency, & Candidate Scoring)
# =============================================================================

def test_build_evidence_dossier_all_xai_branches():
    """Verify all XAI thesis branches, solvency conditions, and risk warnings."""
    # Case 1: High debt to equity (> 150), tight current ratio (< 1.1), positive insider buying
    funds_distressed = {
        "revenue_yoy": 0.45,
        "debt_to_equity": 280.0,
        "current_ratio": 0.85,
        "insider_shares_net": 150000,
    }
    dossier = build_evidence_dossier(
        ticker="DIST_SYM",
        industry="Defense",
        catalyst="Multi-billion Pentagon initiative",
        fundamentals=funds_distressed,
        strategy_track="GROWTH"
    )
    assert dossier["solvency_rating"] == "Distressed"
    # Warnings check
    assert any("Elevated Debt-to-Equity" in w for w in dossier["risk_warnings"])
    assert any("Current ratio is tight" in w for w in dossier["risk_warnings"])
    # Thesis check
    assert any("Revenue growth of 45.0%" in t for t in dossier["bull_thesis"])

    # Case 2: SEC map exception, alternative data exception
    with patch("stockpicker.core.get_sec_ticker_map", side_effect=RuntimeError("SEC unavailable")), \
         patch("stockpicker.alternative_data_client.fetch_alternative_catalysts", side_effect=RuntimeError("Alt data down")):
        safe_dossier = build_evidence_dossier(
            ticker="SAFE_SYM",
            industry="Cloud",
            catalyst="No news available",
            fundamentals={"current_ratio": 1.6, "debt_to_equity": 60.0},
            strategy_track="GROWTH"
        )
        assert safe_dossier["sec_filing_url"] == "https://www.sec.gov/edgar/searchedgar/companysearch"
        assert safe_dossier["solvency_rating"] == "Pristine"


def test_score_candidates_all_branches():
    """Verify score_candidates fundamentals_only, direct_ticker fallback, and candidate fallback."""
    # 1. Skip low explosiveness
    news_low = [{"headline": "Small update", "explosiveness": 4.0, "impacted_us_tickers": ["LOW"]}]
    assert score_candidates(news_low) == []

    # 2. Fundamentals-only mode passes through regardless of explosiveness
    news_fund_only = [{
        "headline": "Fundamental screening pick",
        "news_source": "fundamentals_only",
        "explosiveness": 0.0,
        "industry": "Software",
        "direct_ticker": "FUND_SYM"
    }]
    with patch("stockpicker.core.compute_fundamental_score", return_value={"score": 80.0, "revenue_yoy": 0.25}), \
         patch("stockpicker.core.build_evidence_dossier", return_value={"solvency_rating": "Robust"}):
        res = score_candidates(news_fund_only)
        assert len(res) == 1
        assert res[0]["ticker"] == "FUND_SYM"

    # 3. No candidates in news item
    news_no_cand = [{"headline": "General market news", "explosiveness": 8.5, "industry": "Energy"}]
    assert score_candidates(news_no_cand) == []

    # 4. Fallback scoring when all candidate fundamental scorings fail
    news_fail = [{
        "headline": "Huge contract won",
        "explosiveness": 9.0,
        "industry": "Space",
        "impacted_us_tickers": ["RKLB", ""]  # Hits line 1735 with empty ticker
    }]
    with patch("stockpicker.core.compute_fundamental_score", side_effect=Exception("API limit exceeded")), \
         patch("stockpicker.core.build_evidence_dossier", return_value={"solvency_rating": "Adequate"}):
        fallback_picks = score_candidates(news_fail)
        assert len(fallback_picks) == 1
        assert fallback_picks[0]["ticker"] == "RKLB"
        assert "baseline score" in fallback_picks[0]["fundamental_reasons"]


def test_hydrate_history_state_picks_array_branches():
    """Verify hydrate_history_state parsing picks array with ACCEPTED, PROVISIONED, REJECTED, and empty ticker."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "already_accepted": [{"ticker": "AA1"}],
        "picks": [
            {"ticker": "AC1", "status": "ACCEPTED"},
            {"symbol": "PR1", "status": "PROVISIONED"},
            {"ticker": "RJ1", "status": "REJECTED"},
            {"ticker": "IGN1", "status": "PENDING_REVIEW"},
            {"ticker": ""}  # Empty symbol should continue
        ],
        "rejected_cooldown": [{"ticker": "RC1"}]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    state = hydrate_history_state(mock_db)
    assert "AA1" in state["accepted"]
    assert "AC1" in state["accepted"]
    assert "PR1" in state["accepted"]
    assert "RJ1" in state["rejected"]
    assert "RC1" in state["rejected"]


def test_runner_income_fresh_lead_and_expired_rejection_promotion():
    """Verify runner promotion of fresh income candidates and expired rejections (> 7 days)."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    now_utc = datetime.now(timezone.utc)
    eight_days_ago = (now_utc - timedelta(days=8)).isoformat()

    # Old rejection should be promoted because it is outside the 7-day cooldown
    history = {
        "accepted": {},
        "rejected": {
            "OLD_REJ": {"score": 50.0, "rejected_at": eight_days_ago}
        }
    }

    # Growth lead with expired rejection, plus fresh income candidate (hits line 376)
    growth_cand = [{"ticker": "OLD_REJ", "composite_score": 55.0, "explosiveness": 8.5}]
    income_cand = [{"ticker": "INCOME_LEAD", "income_score": 82.0, "explosiveness": 7.8}]

    with patch("stockpicker.runner.load_registered_bots", return_value={}), \
         patch("stockpicker.runner.hydrate_history_state", return_value=history), \
         patch("stockpicker.runner.fetch_all_news", return_value=[{"headline": "test"}]), \
         patch("stockpicker.runner.rank_news", return_value=[{"headline": "test", "explosiveness": 8.0}]), \
         patch("stockpicker.runner.score_candidates", return_value=growth_cand), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=income_cand), \
         patch("stockpicker.core.build_evidence_dossier", return_value={"solvency_rating": "Robust"}), \
         patch("google.cloud.firestore.Client", return_value=mock_db):

        result = run_stockpicker()
        # Verify OLD_REJ was promoted (hits line 357 is_in_cooldown=False)
        growth_tickers = [x["ticker"] for x in result["growth_picks"]]
        assert "OLD_REJ" in growth_tickers

        # Verify INCOME_LEAD was appended as actionable income lead (hits line 376)
        income_tickers = [x["ticker"] for x in result["income_picks"]]
        assert "INCOME_LEAD" in income_tickers

        # Verify average explosiveness calculated (hits line 399)
        assert result["avg_explosiveness"] > 0.0


def test_build_evidence_dossier_sec_cik_and_congress_and_earnings_risk():
    """Verify dossier builds SEC CIK URL, congress bull thesis, and earnings assignment risk."""
    # 1. CIK found -> hits line 1581
    # 2. Congress trade found without contracts -> hits line 1618
    # 3. Income track with days_to_earnings <= 30 -> hits line 1642
    with patch("stockpicker.core.get_sec_ticker_map", return_value={"CONG_SYM": "0001234567"}), \
         patch("stockpicker.alternative_data_client.fetch_alternative_catalysts", return_value={
             "contracts": [],
             "congress_trades": [{"representative": "Rep Smith", "transaction_date": "2026-03-01"}],
             "total_contract_value": 0
         }):
        dossier = build_evidence_dossier(
            ticker="CONG_SYM",
            industry="Semiconductors",
            catalyst="",
            fundamentals={"revenue_yoy": None, "current_ratio": 1.5, "debt_to_equity": 50.0},
            strategy_track="GROWTH"
        )
        assert dossier["sec_filing_url"] == "https://www.sec.gov/edgar/browse/?CIK=0001234567"
        assert any("Rep Smith" in t for t in dossier["bull_thesis"])

    # Test Income track with earnings scheduled <= 30 days
    with patch("stockpicker.core.get_sec_ticker_map", return_value={}), \
         patch("stockpicker.alternative_data_client.fetch_alternative_catalysts", return_value={"contracts": [], "congress_trades": []}):
        dossier_inc = build_evidence_dossier(
            ticker="INC_SYM",
            industry="Finance",
            catalyst="High dividend",
            fundamentals={"current_ratio": 1.5, "debt_to_equity": 50.0},
            strategy_track="INCOME",
            options_data={
                "monthly_yield_est": 2.5,
                "annualized_yield_est": 30.0,
                "strike": 105.0,
                "open_interest": 800,
                "days_to_earnings": 14,
                "earnings_risk_flag": True
            }
        )
        assert any("Earnings scheduled in 14 days" in w for w in dossier_inc["risk_warnings"])


def test_runner_cli_main_block():
    """Verify CLI entry point handling of success and failure outcomes."""
    from stockpicker.runner import main as runner_main

    # Case 1: Success with picks
    with patch("stockpicker.runner.run_stockpicker", return_value={"picks": [1, 2]}):
        assert runner_main() == 0

    # Case 2: Success with empty picks
    with patch("stockpicker.runner.run_stockpicker", return_value={}):
        assert runner_main() == 0

    # Case 3: Fatal error
    with patch("stockpicker.runner.run_stockpicker", side_effect=RuntimeError("Fatal CLI crash")):
        assert runner_main() == 1


def test_bot_focus_stockpicker_header_tile_presence():
    """Verify templates/bot_focus.html retains the Stock Picker title header card

    and keeps the navigation bar and tile in identical alignment to companion pages.
    """
    html_path = os.path.join(os.path.dirname(__file__), "../templates/bot_focus.html")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Header card exists with correct classes and identifiers
    assert 'id="stockPickerHeader"' in content
    assert 'class="bot-header stock-picker-header"' in content
    assert 'id="botFocusHeader"' in content

    # 2. Contains proper title, icon, and Purpose metadata
    assert "Stock Picker" in content
    assert "📊" in content
    assert "Purpose:" in content
    assert "Dual-track growth equity &amp; covered call income screener" in content

    # 3. Mode CSS correctly manages header display and risk bar suppression
    assert "body.stock-picker-mode #stockPickerHeader" in content
    assert "display: flex !important;" in content
    assert "body.stock-picker-mode #botFocusHeader" in content
    assert "body.stock-picker-mode .market-clock" in content
    assert "display: none !important;" in content

    # 4. JS functions exist for bidirectional tab switching
    assert "function showStockPickerTab(" in content
    assert "function showBotFocusTab(" in content

    # 5. showBotFocusTab restores all bot-focus sections and main-grid
    assert "section:not(#stock-picker-section)" in content
    assert "body.stock-picker-mode .main-grid" in content
    assert "body.stock-picker-mode section:not(#stock-picker-section)" in content
    assert "body.stock-picker-mode #stock-picker-section" in content
    assert "display: block !important;" in content



