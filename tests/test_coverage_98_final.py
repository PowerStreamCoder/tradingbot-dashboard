"""
Comprehensive targeted tests covering remaining edge-case statements across:
- services/pnl_statement_engine.py
- stockpicker/sentinel.py
- stockpicker/runner.py
- stockpicker/income_screener.py
- stockpicker/feedback_engine.py
- stockpicker/core.py
- main.py
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta, timezone, UTC
from unittest.mock import MagicMock, patch, mock_open
import pytest
import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main
from main import (
    app,
    dashboard_data,
    load_bot_configs,
    load_bot_parameters,
    authenticated_sessions,
    get_client_id_for_bot,
    DashboardData,
    root,
    bot_focus,
    pnl_reporting,
    health_check,
    get_pnl_statement,
    _stock_picks_cache,
    _trade_history_cache,
)


@pytest.fixture
def auth_client():
    client = TestClient(app)
    session_id = "test-cov-session-final"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=4)
    client.cookies.set("dashboard_session", session_id)
    yield client, session_id
    authenticated_sessions.pop(session_id, None)


# ============================================================================
# 1. SERVICES/PNL_STATEMENT_ENGINE.PY
# ============================================================================

def test_pnl_engine_remaining_branches():
    import services.pnl_statement_engine
    from services.pnl_statement_engine import aggregate_portfolio_pnl

    configs = [{"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "trading_mode": "live"}]
    bot_overview = {
        "nvda": {"symbol": "NVDA", "position_size": 10, "current_price": 100.0, "trading_mode": "live"}
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    trades = [
        # Match via client_id (lines 374-375)
        {"timestamp": now_iso, "symbol": "", "botId": 4, "trading_mode": "paper", "profitLoss": 50.0},
        # Unmapped trade (line 380)
        {"timestamp": now_iso, "symbol": "UNKNOWN", "botId": 999, "trading_mode": "paper", "profitLoss": 10.0},
    ]

    res = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="paper", bot_scope="ALL", period="ALL")
    assert res is not None

    # bot_scope not in symbols_for_mode (line 430)
    live_trades = [{"timestamp": now_iso, "symbol": "NVDA", "botId": 4, "trading_mode": "live", "profitLoss": 50.0}]
    res_scope = aggregate_portfolio_pnl(live_trades, {}, configs, mode="paper", bot_scope="NVDA", period="ALL")
    assert res_scope is not None
    assert res_scope.bot_scope == "NVDA"

    # Line 647: ts is None in heatmap loop
    real_parse = services.pnl_statement_engine.parse_iso_datetime
    call_counts = [0]
    def mock_parse(val):
        call_counts[0] += 1
        if call_counts[0] > 1:
            return None
        return real_parse(val)

    with patch("services.pnl_statement_engine.parse_iso_datetime", side_effect=mock_parse):
        res_hm = aggregate_portfolio_pnl([trades[0]], bot_overview, configs, mode="paper", period="ALL")
        assert res_hm is not None


# ============================================================================
# 2. STOCKPICKER/SENTINEL.PY
# ============================================================================

def test_sentinel_remaining_exceptions():
    from stockpicker.sentinel import _hydrate_active_positions, _persist_telemetry

    mock_db = MagicMock()
    # Line 240-241: Exception reading stock_picks/current
    mock_db.collection.return_value.document.return_value.get.side_effect = Exception("firestore err")
    with patch("builtins.open", side_effect=Exception("no bots.json")):
        positions = _hydrate_active_positions(mock_db)
        assert isinstance(positions, list)

    # Line 261-262: Exception writing telemetry to bots collection
    mock_db2 = MagicMock()
    mock_db2.collection.return_value.document.return_value.set.side_effect = Exception("bots set err")
    pos_res = {
        "symbol": "NVDA",
        "client_id": 4,
        "thesis_telemetry": {"thesis_state": "HEALTHY", "action_instruction": "HOLD"}
    }
    _persist_telemetry(mock_db2, pos_res)


# ============================================================================
# 3. STOCKPICKER/RUNNER.PY
# ============================================================================

def test_runner_remaining_branches():
    from stockpicker.runner import run_stockpicker

    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    # Lines 270, 502-503, 521-522: load adjustments, archive error, feedback error
    with patch("google.cloud.firestore.Client", return_value=mock_db), \
         patch("stockpicker.runner.load_weight_adjustments", return_value={"momentum": 1.1}), \
         patch("stockpicker.core.fetch_all_news", return_value=[]), \
         patch("stockpicker.core.generate_fundamentals_picks", return_value=[{"ticker": "NVDA", "composite_score": 88.0}]), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
         patch("stockpicker.runner.load_registered_bots", return_value={}), \
         patch("stockpicker.runner.archive_run_snapshots", side_effect=Exception("archive error")), \
         patch("stockpicker.runner.run_feedback_loop", side_effect=Exception("feedback error")):
        res = run_stockpicker()
        assert res is not None
        assert res["status"] == "success"

    # Line 272: load_weight_adjustments error
    with patch("google.cloud.firestore.Client", return_value=mock_db), \
         patch("stockpicker.runner.load_weight_adjustments", side_effect=Exception("load adj err")), \
         patch("stockpicker.core.fetch_all_news", return_value=[]), \
         patch("stockpicker.core.generate_fundamentals_picks", return_value=[]), \
         patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
         patch("stockpicker.runner.load_registered_bots", return_value={}):
        res2 = run_stockpicker()
        assert res2 is not None


# ============================================================================
# 4. STOCKPICKER/INCOME_SCREENER.PY
# ============================================================================

def test_income_screener_remaining_branches():
    from stockpicker.income_screener import (
        fetch_earnings_date,
        fetch_ticker_quote_metrics,
        evaluate_covered_call_candidate,
        screen_income_candidates,
        _clear_income_screener_caches,
    )
    import requests

    _clear_income_screener_caches()

    # Lines 141-145: Yahoo 429/401 retry with crumb refresh
    with patch("requests.get") as mock_get, \
         patch("stockpicker.income_screener.get_yahoo_crumb_and_cookies", return_value=("new_crumb", {"B": "cookie"})):
        mock_resp_429 = MagicMock(status_code=429)
        mock_resp_200 = MagicMock(
            status_code=200,
            json=lambda: {"quoteSummary": {"result": [{"calendarEvents": {"earnings": {"earningsDate": [{"raw": int(time.time() + 86400 * 20)}]}}}]}}
        )
        mock_get.side_effect = [mock_resp_429, mock_resp_200]
        dt = fetch_earnings_date("RETRY_CRUMB_SYM")
        assert dt is not None

    # Lines 198-204: Yahoo chart quote success
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"chart": {"result": [{"meta": {"regularMarketPrice": 120.0, "fiftyTwoWeekHigh": 130.0, "fiftyTwoWeekLow": 100.0}}]}}
        )
        metrics = fetch_ticker_quote_metrics("CHART_SYM")
        assert metrics is not None
        assert metrics["current_price"] == 120.0

    # Lines 222-223: Finnhub quote throws exception
    with patch("requests.get") as mock_get, \
         patch.dict(os.environ, {"FINNHUB_API_KEY": "test_fh_key"}):
        mock_get.side_effect = Exception("network boom")
        assert fetch_ticker_quote_metrics("FAIL_FH_SYM") is None

    # Lines 358 & 390: Yield tier & ATR tiers
    call_low_yield = {
        "strike": 105.0, "bid": 0.15, "ask": 0.20, "lastPrice": 0.15, "openInterest": 150, "impliedVolatility": 0.25
    }
    opt_data = {
        "quote": {"regularMarketPrice": 10.0, "fiftyTwoWeekHigh": 18.0, "fiftyTwoWeekLow": 8.0},  # high ATR > 0.04 (line 390)
        "options": [{"calls": [call_low_yield]}]  # monthly_yield = 0.015 (line 358)
    }
    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=opt_data), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(UTC) + timedelta(days=25)), \
         patch("stockpicker.core.get_finnhub_insider_trades", side_effect=Exception("insider err")), \
         patch("stockpicker.alternative_data_client.fetch_congressional_trades", side_effect=Exception("congress err")):
        cand = evaluate_covered_call_candidate("TIER_SYM")
        assert cand is not None
        assert cand["income_score"] > 0

    # Lines 358 & 390: evaluate_covered_call_candidate thin yield & wide spread
    call_thin = {
        "strike": 103.0, "bid": 0.50, "ask": 2.00, "lastPrice": 0.50, "openInterest": 150, "impliedVolatility": 0.25
    }
    opt_data_thin = {
        "quote": {"regularMarketPrice": 100.0, "fiftyTwoWeekHigh": 110.0, "fiftyTwoWeekLow": 90.0},
        "options": [{"calls": [call_thin]}]
    }
    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=opt_data_thin), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(UTC) + timedelta(days=50)), \
         patch("stockpicker.core.get_finnhub_insider_trades", return_value={"insider_score": 0.0}), \
         patch("stockpicker.alternative_data_client.fetch_congressional_trades", return_value=[]):
        cand_thin = evaluate_covered_call_candidate("THIN_SYM")
        assert cand_thin is not None
        assert cand_thin["income_score"] > 0

    # Lines 487-488: screen_income_candidates with integer tickers argument
    with patch("stockpicker.income_screener.evaluate_covered_call_candidate", return_value=None):
        assert screen_income_candidates(3) == []


# ============================================================================
# 5. STOCKPICKER/FEEDBACK_ENGINE.PY
# ============================================================================

def test_feedback_engine_remaining_branches():
    from stockpicker.feedback_engine import (
        compute_forward_alpha,
        compute_factor_ic,
        load_weight_adjustments,
        ingest_execution_feedback,
        compute_execution_summary,
        analyze_rejection_patterns,
        cleanup_stale_archives,
        _extract_factor_scores,
    )

    mock_db = MagicMock()

    # Lines 263, 272-274, 281-283, 286: compute_forward_alpha edge docs
    doc_missing_entry = MagicMock(to_dict=lambda: {"pick_id": "p1", "ticker": "AAPL", "run_date": "2026-10-01", "entry_price": None})
    doc_bad_date = MagicMock(to_dict=lambda: {"pick_id": "p2", "ticker": "AAPL", "run_date": "INVALID", "entry_price": 100.0})
    doc_price_err = MagicMock(to_dict=lambda: {"pick_id": "p3", "ticker": "AAPL", "run_date": "2026-10-01", "entry_price": 100.0})
    doc_price_none = MagicMock(to_dict=lambda: {"pick_id": "p4", "ticker": "AAPL", "run_date": "2026-10-01", "entry_price": 100.0})

    mock_db.collection("stock_picks_archive").where.return_value.stream.return_value = [
        doc_missing_entry, doc_bad_date, doc_price_err, doc_price_none
    ]

    with patch("stockpicker.feedback_engine._fetch_current_price") as mock_cur, \
         patch("stockpicker.feedback_engine._fetch_historical_price", return_value=100.0):
        mock_cur.side_effect = [Exception("price fetch err"), None, 110.0]
        res = compute_forward_alpha(mock_db)
        assert "picks_evaluated" in res

    # Lines 321-323: forward alpha global error
    mock_db_fail = MagicMock()
    mock_db_fail.collection("stock_picks_archive").where.side_effect = Exception("where err")
    assert len(compute_forward_alpha(mock_db_fail)["errors"]) > 0

    # Line 439: Factor IC in neutral zone (0 < ic <= 0.05) & Lines 459-460: ledger write exception
    mock_ic_db = MagicMock()
    mock_rec_doc = MagicMock()
    mock_rec_doc.to_dict.return_value = {
        "ticker": "AAPL", "alpha_t5": 0.02, "factor_scores": {"relative_volume": 4.0}
    }
    mock_ic_db.collection("stock_picks_archive").where.return_value.stream.return_value = [mock_rec_doc] * 5
    mock_ic_db.collection("stockpicker_factor_ledger").document.return_value.set.side_effect = Exception("set ledger fail")
    with patch("stockpicker.feedback_engine._spearman_rank_correlation", return_value=0.02):
        ledger = compute_factor_ic(mock_ic_db)
        assert ledger is not None

    # Lines 504-506: load_weight_adjustments exception
    mock_bad_db = MagicMock()
    mock_bad_db.collection("stockpicker_factor_ledger").document.return_value.get.side_effect = Exception("ledger read err")
    assert load_weight_adjustments(mock_bad_db) == {}

    # Line 577: ingest_execution_feedback archive doc does not exist
    mock_fb_db = MagicMock()
    mock_fb_db.collection("stockpicker_execution_feedback").document.return_value.set.return_value = None
    mock_no_archive = MagicMock()
    mock_no_archive.exists = False
    mock_fb_db.collection("stock_picks_archive").document.return_value.get.return_value = mock_no_archive
    res_ingest = ingest_execution_feedback(mock_fb_db, "PICK_123", 50.0, 3, "target_hit")
    assert res_ingest["status"] == "success"

    # Lines 582-584: ingest_execution_feedback exception
    mock_fb_err_db = MagicMock()
    mock_fb_err_db.collection("stockpicker_execution_feedback").document.return_value.set.side_effect = Exception("fs boom")
    assert ingest_execution_feedback(mock_fb_err_db, "PICK_123", 50.0, 3, "target_hit")["status"] == "error"

    # Line 678: stop_out_rate > 0.60
    mock_exec_db = MagicMock()
    mock_exec_docs = [
        MagicMock(to_dict=lambda: {"realized_pnl": -10.0, "exit_reason": "atr_stop_loss", "trade_duration_days": 2})
        for _ in range(5)
    ]
    mock_exec_db.collection("stockpicker_execution_feedback").where.return_value.stream.return_value = mock_exec_docs
    summary = compute_execution_summary(mock_exec_db)
    assert len(summary["self_healing_flags"]) > 0

    # Lines 687-689: compute_execution_summary exception
    mock_exec_err_db = MagicMock()
    mock_exec_err_db.collection("stockpicker_execution_feedback").where.side_effect = Exception("query boom")
    assert compute_execution_summary(mock_exec_err_db)["status"] == "error"

    # Line 720: analyze_rejections with empty rejected_cooldown
    mock_empty_rej = MagicMock(exists=True, to_dict=lambda: {"rejected_cooldown": []})
    mock_rej_empty_db = MagicMock()
    mock_rej_empty_db.collection("stock_picks").document.return_value.get.return_value = mock_empty_rej
    assert analyze_rejection_patterns(mock_rej_empty_db)["status"] == "no_rejections"

    # Lines 787-788: analyze_rejection_patterns write exception & 792-794: global error
    mock_rej_db = MagicMock()
    mock_rej_doc = MagicMock()
    mock_rej_doc.exists = True
    mock_rej_doc.to_dict.return_value = {"rejected_cooldown": [{"rejection_reason": "WEAK_CATALYST"}]}
    mock_rej_db.collection("stock_picks").document.return_value.get.return_value = mock_rej_doc
    mock_rej_db.collection("stockpicker_rejection_analysis").document.return_value.set.side_effect = Exception("write fail")
    res_rej = analyze_rejection_patterns(mock_rej_db)
    assert res_rej["status"] == "success"

    mock_rej_err = MagicMock()
    mock_rej_err.collection("stock_picks").document.return_value.get.side_effect = Exception("read fail")
    assert analyze_rejection_patterns(mock_rej_err)["status"] == "error"

    # Lines 834-835, 856-857, 866, 869-870: cleanup_stale_archives deletion and query errors
    mock_clean_query_fail = MagicMock()
    mock_clean_query_fail.collection("stock_picks_archive").where.side_effect = Exception("archive q fail")
    mock_clean_query_fail.collection("stock_picks").stream.side_effect = Exception("daily q fail")
    mock_clean_query_fail.collection("stockpicker_execution_feedback").where.side_effect = Exception("fb q fail")
    res_q_fail = cleanup_stale_archives(mock_clean_query_fail, ttl_days=30)
    assert len(res_q_fail["deleted"]["errors"]) == 3

    doc_clean_fail = MagicMock()
    doc_clean_fail.reference.delete.side_effect = Exception("del err")
    doc_clean_fail.id = "doc1"
    doc_fb_ok = MagicMock(id="fb_ok")

    mock_clean_db = MagicMock()
    mock_clean_db.collection("stock_picks_archive").where.return_value.limit.return_value.stream.return_value = [doc_clean_fail]
    mock_clean_db.collection("stock_picks").stream.return_value = []
    mock_clean_db.collection("stockpicker_execution_feedback").where.return_value.limit.return_value.stream.return_value = [doc_fb_ok]

    clean_res = cleanup_stale_archives(mock_clean_db, ttl_days=30)
    assert clean_res["deleted"]["feedback"] == 1

    # Lines 992-993: _extract_factor_scores match group ValueError
    assert isinstance(_extract_factor_scores({"fundamental_reasons": "congress (..)"}), dict)
    assert isinstance(_extract_factor_scores({"fundamental_reasons": "rel_vol=INVALIDx (+NAN)"}), dict)


# ============================================================================
# 6. STOCKPICKER/CORE.PY
# ============================================================================

def test_core_remaining_branches():
    from stockpicker.core import (
        rate_limited_get,
        fetch_newsapi,
        fetch_x_recent,
        fetch_polygon_benzinga,
        fetch_finnhub_news,
        fetch_alphavantage_news,
        rank_news,
        NewsItem,
        get_sec_ticker_map,
        get_companyfacts_quarterly,
        get_yahoo_financial_snapshot,
        get_alpha_earnings,
        compute_fundamental_score,
        build_evidence_dossier,
        _api_call_times,
    )
    import requests

    # Lines 238-240: rate_limited_get sleep
    _api_call_times.clear()
    now_t = time.time()
    for _ in range(10):
        _api_call_times.append(now_t)
    with patch("time.sleep") as mock_sleep, patch("requests.get"):
        rate_limited_get("https://example.com")
        mock_sleep.assert_called()

    # Lines 304-306, 379-381, 438-440, 506-508, 695-698: Exceptions in news fetching
    with patch.dict(os.environ, {
        "NEWSAPI_KEY": "k1", "X_BEARER_TOKEN": "k2", "POLYGON_API_KEY": "k3",
        "FINNHUB_API_KEY": "k4", "ALPHAVANTAGE_API_KEY": "k5"
    }), patch("requests.get", side_effect=requests.RequestException("net err")):
        assert fetch_newsapi("2026-10-10T00:00:00Z") == []
        assert fetch_x_recent() == []
        assert fetch_polygon_benzinga("2026-10-10") == []
        assert fetch_finnhub_news("2026-10-10T00:00:00Z") == []
        assert fetch_alphavantage_news("2026-10-10T00:00:00Z") == []

    # Lines 1051-1064: rank_news with NewsItem list
    items = [
        NewsItem(
            source_type="news", source_name="Reuters", published_at="2026-10-10T12:00:00Z",
            headline="FDA approved new drug", summary="Clinical phase 3 passed", url="http://test.com",
            engagement=100.0, tickers="BMRN", raw_json="{}"
        )
    ]
    with patch("stockpicker.core.call_llm_rank", return_value=[{"ticker": "BMRN", "explosiveness": 8.5}]):
        res_ranked = rank_news(items)
        assert len(res_ranked) == 1

    # Lines 1104-1107, 1183-1185, 1235-1238, 1286-1288: Helpers exceptions
    from stockpicker.core import _clear_core_caches
    _clear_core_caches()
    with patch("stockpicker.core.rate_limited_get", side_effect=Exception("sec err")):
        assert get_sec_ticker_map() == {}
        assert get_companyfacts_quarterly("AAPL") == {}

    with patch("requests.get", side_effect=Exception("yf err")):
        assert get_yahoo_financial_snapshot("AAPL") == {}
        assert get_alpha_earnings("AAPL") == {}

    # Lines 1560-1565 & 1575-1583 & 1667-1677 & 1695 & 1742-1745: compute_fundamental_score branches
    mock_sec = {"revenue": [("2025Q3", 100), ("2026Q3", 150)], "net_income": [("2026Q3", 25)], "operating_income": [("2026Q3", 30)]}
    mock_yf = {
        "earningsHistory": {"history": [{"surprisePercent": {"raw": 0.18}}]},
        "price": {"regularMarketVolume": 5000000, "averageVolume": 2000000, "regularMarketPreviousClose": 100.0},  # rel_vol > 2.0 (line 1668)
        "summaryDetail": {"recommendationMean": 4.0},  # recommendation >= 3.5 (line 1695)
    }
    mock_av = {"quarterlyEarnings": [{"surprisePercentage": "15.5"}]}
    with patch("stockpicker.core.get_companyfacts_quarterly", return_value=mock_sec), \
         patch("stockpicker.core.get_yahoo_financial_snapshot", return_value=mock_yf), \
         patch("stockpicker.core.get_alpha_earnings", return_value=mock_av), \
         patch("stockpicker.alternative_data_client.compute_congressional_trading_score", side_effect=Exception("congress err")):
        score_res = compute_fundamental_score("AAPL_BRANCH")
        assert score_res["score"] > 0

    # Test rel_vol <= 0.5 (line 1673)
    mock_yf_low_vol = {
        "price": {"regularMarketVolume": 400000, "averageVolume": 1000000, "regularMarketPreviousClose": 100.0},
    }
    with patch("stockpicker.core.get_companyfacts_quarterly", return_value=mock_sec), \
         patch("stockpicker.core.get_yahoo_financial_snapshot", return_value=mock_yf_low_vol), \
         patch("stockpicker.core.get_alpha_earnings", return_value={}):
        score_low = compute_fundamental_score("AAPL_LOW_VOL")
        assert score_low["score"] > 0

    # Lines 607-608 & 630-632: get_finnhub_insider_trades
    from stockpicker.core import (
        get_finnhub_insider_trades,
        call_llm_rank,
        generate_fundamentals_picks,
        get_finnhub_earnings_surprise,
        safe_yoy_from_quarters,
        score_candidates,
        _av_earnings_cache,
        _finnhub_earnings_cache,
        _fundamental_scores_cache,
    )
    with patch("stockpicker.core.env", return_value="fake_key"), \
         patch("requests.get") as mock_fh_get:
        mock_fh_get.return_value.status_code = 200
        mock_fh_get.return_value.json.return_value = {
            "data": [{"change": 10, "share": 100, "filingDate": "2026-10-01", "transactionCode": "P"}]
        }
        res_ins = get_finnhub_insider_trades("INS_BUY")
        assert res_ins["insider_score"] > 0

        mock_fh_get.side_effect = Exception("insider boom")
        assert get_finnhub_insider_trades("INS_ERR")["insider_score"] == 0.0

    # Lines 940-941: call_llm_rank gemini returns non-list
    with patch("stockpicker.core.env", return_value="gem_key"), \
         patch("requests.post") as mock_gem_post:
        mock_gem_post.return_value.status_code = 200
        mock_gem_post.return_value.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"not": "a list"}'}]}}]
        }
        ranked_hlp = call_llm_rank([{"headline": "test"}])
        assert isinstance(ranked_hlp, list)

    # Lines 1002-1003: generate_fundamentals_picks exception scoring
    with patch("stockpicker.core.SECTOR_TO_CANDIDATES", {"Tech": ["FAIL_FUND"]}), \
         patch("stockpicker.core.compute_fundamental_score", side_effect=Exception("fund score boom")):
        assert generate_fundamentals_picks() == []

    # Lines 1326-1328 & 1332-1333 & 1343 & 1352-1355: get_alpha_earnings
    _av_earnings_cache["CACHED_AV"] = (time.time(), {"quarterlyEarnings": [{"surprisePercentage": "10"}]})
    assert "quarterlyEarnings" in get_alpha_earnings("CACHED_AV")
    with patch("stockpicker.core.env", return_value=""):
        assert get_alpha_earnings("NO_KEY") == {}
    with patch("stockpicker.core.env", return_value="fake_key"), patch("requests.get") as mock_av_req:
        mock_av_req.return_value.status_code = 500
        assert get_alpha_earnings("ERR_STATUS") == {}
        mock_av_req.return_value.status_code = 200
        mock_av_req.return_value.json.return_value = {"quarterlyEarnings": []}
        assert get_alpha_earnings("OK_AV") == {"quarterlyEarnings": []}

    # Lines 1372-1374 & 1378 & 1391-1392: get_finnhub_earnings_surprise
    _finnhub_earnings_cache["CACHED_FH"] = (time.time(), 0.05)
    assert get_finnhub_earnings_surprise("CACHED_FH") == 0.05
    with patch("stockpicker.core.env", return_value=""):
        assert get_finnhub_earnings_surprise("NO_KEY_FH") is None
    with patch("stockpicker.core.env", return_value="fake_key"), patch("stockpicker.core.safe_get", side_effect=Exception("fh boom")):
        assert get_finnhub_earnings_surprise("BOOM_FH") is None

    # Lines 1455-1456: safe_yoy_from_quarters invalid value
    assert safe_yoy_from_quarters([{"val": "invalid"}, {}, {}, {}, {}]) is None

    # Line 1527: compute_fundamental_score cache hit
    cache_k = ("CACHED_FUND", ())
    _fundamental_scores_cache[cache_k] = {"score": 95.0}
    assert compute_fundamental_score("CACHED_FUND")["score"] == 95.0

    # Lines 1947 & 1949: build_evidence_dossier missing branches
    doss_deg1 = build_evidence_dossier("DEG1", "Tech", "Cat", {"missing_sources": ["SEC EDGAR", "Yahoo Finance"]})
    assert doss_deg1["data_quality"] == "DEGRADED"
    doss_deg2 = build_evidence_dossier("DEG2", "Tech", "Cat", {"missing_sources": ["S1", "S2", "S3"]})
    assert doss_deg2["data_quality"] == "DEGRADED"

    # Line 2114: score_candidates degraded with missing sources
    cand_item = {
        "explosiveness": 8.0,
        "headline": "Tech catalyst",
        "industry": "Semiconductors",
        "impacted_us_tickers": ["NVDA"],
        "rationale_short": "Short rat",
        "thesis": "Thesis"
    }
    with patch("stockpicker.core.compute_fundamental_score", return_value={"score": 80.0, "is_degraded": True, "missing_sources": ["SEC"]}):
        g_picks = score_candidates([cand_item])
        assert len(g_picks) > 0


# ============================================================================
# 7. MAIN.PY
# ============================================================================

def test_main_remaining_branches(auth_client):
    client, _ = auth_client

    # Lines 198-199: load_bot_configs with FileNotFoundError
    main._bot_config_cache['config'] = None
    main._bot_config_cache['mtime'] = None
    with patch("builtins.open", side_effect=FileNotFoundError("no bots.json")):
        with pytest.raises(FileNotFoundError):
            load_bot_configs()

    # Lines 281-283: get_client_id_for_bot with FileNotFoundError
    with patch("main.load_bot_configs", side_effect=FileNotFoundError("no bots.json")):
        assert get_client_id_for_bot(99) == 99

    # Lines 441-442: Middleware exception deleting expired session
    with patch.object(main.sessions_ref, "document") as mock_doc_ref:
        mock_doc_ref.return_value.delete.side_effect = Exception("delete fail")
        unauthed_client = TestClient(app)
        expired_id = "expired-sess-id"
        main.authenticated_sessions[expired_id] = datetime.now() - timedelta(hours=1)
        unauthed_client.cookies.set("dashboard_session", expired_id)
        res = unauthed_client.get("/api/trade-history")
        assert res.status_code == 401

    # Line 474: Middleware non-API request without auth returns call_next
    unauthed_client2 = TestClient(app)
    res_other = unauthed_client2.get("/non-existent-static-file.png")
    assert res_other.status_code == 404

    # Lines 608-613: DashboardData __init__ exception
    with patch("google.cloud.firestore.Client", side_effect=Exception("fs init err")):
        dd = DashboardData()
        assert dd.bot_data == {}

    # Lines 800-801: Login exception saving session to Firestore
    valid_code = datetime.now().strftime("%d%m%y")
    with patch.object(main.sessions_ref, "document") as mock_sess_doc:
        mock_sess_doc.return_value.set.side_effect = Exception("save sess fail")
        res_login = client.post("/authenticate", json={"access_code": valid_code})
        assert res_login.status_code == 200

    # Line 826: direct call to root()
    import asyncio
    asyncio.run(root())

    # Lines 843-844 & 859-860: FileNotFoundError in bot_focus and pnl_reporting
    with patch("main.FileResponse", side_effect=FileNotFoundError("not found")):
        with pytest.raises(main.HTTPException):
            asyncio.run(bot_focus())
        with pytest.raises(main.HTTPException):
            asyncio.run(pnl_reporting())

    # Lines 891-893 & 912-914: P&L statement account_overview and error paths
    mock_acct_doc = MagicMock()
    mock_acct_doc.exists = True
    mock_acct_doc.to_dict.side_effect = Exception("acct read err")
    with patch.object(dashboard_data.db, "collection") as mock_coll:
        mock_coll.return_value.document.return_value.get.return_value = mock_acct_doc
        res_pnl = client.get("/api/pnl-statement")
        assert res_pnl.status_code == 200

    with patch("main.aggregate_portfolio_pnl", side_effect=Exception("aggregate err")):
        res_pnl_err = client.get("/api/pnl-statement")
        assert res_pnl_err.status_code == 500

    # Lines 1214, 1226-1227, 1234-1237: get_bot_overview branches
    with patch.object(dashboard_data, "_load_bot_data", return_value={}):
        res_empty_bot = client.get("/api/bot-overview")
        assert res_empty_bot.json() == {}

    raw_bot_data = {
        "bot_1": {"symbol": "NVDA", "bucket_0": {"entryPrice": 120.0, "shares": 10}, "other_field": 123}
    }
    with patch.object(dashboard_data, "_load_bot_data", return_value=raw_bot_data):
        res_bot = client.get("/api/bot-overview")
        assert "bot_1" in res_bot.json()

    with patch.object(dashboard_data, "_load_bot_data", side_effect=Exception("bot data err")):
        res_bot_err = client.get("/api/bot-overview")
        assert res_bot_err.status_code == 200

    # Lines 1357-1359 & 1371-1376: Logs and trade history errors
    with patch.object(dashboard_data, "logs", MagicMock(get=MagicMock(side_effect=Exception("logs err")))):
        res_logs_err = client.get("/api/logs?botId=1")
        assert res_logs_err.status_code == 200
        assert res_logs_err.json() == []

    with patch.object(dashboard_data, "_load_trades", side_effect=Exception("trades err")):
        res_trades_err = client.get("/api/trade-history")
        assert res_trades_err.status_code == 200
        assert res_trades_err.json()["status"] == "error"

    # Lines 1406-1410 & 1433 & 1438-1439: get_all_trade_history cache HIT and trade filtering
    with _trade_history_cache['lock']:
        _trade_history_cache['data'] = [{"symbol": "NVDA", "profitLoss": 100.0}]
        _trade_history_cache['timestamp'] = datetime.now()
    res_cached = client.get("/api/trade-history/all")
    assert res_cached.status_code == 200

    # Reset cache to test filtering
    with _trade_history_cache['lock']:
        _trade_history_cache['data'] = None
        _trade_history_cache['timestamp'] = None

    test_trades_docs = [
        MagicMock(to_dict=lambda: {"symbol": "TEST", "botName": "TEST", "timestamp": "2026-10-10T10:00:00Z"}),
        MagicMock(to_dict=lambda: {"symbol": "NVDA", "botName": "nvda", "timestamp": "2026-10-10T11:00:00Z"}), # missing sequence_number/seqNum
    ]
    with patch.object(dashboard_data.trades_ref, "where") as mock_w:
        mock_w.return_value.stream.return_value = test_trades_docs
        res_trades_stream = client.get("/api/trade-history/all")
        assert res_trades_stream.status_code == 200

    # Lines 1524, 1546, 1579, 1582: Bot stats branches
    stat_trades = [
        MagicMock(to_dict=lambda: {"symbol": "TEST", "botId": 1, "timestamp": "2026-10-10T10:00:00"}), # line 1524
        MagicMock(to_dict=lambda: {"symbol": "NVDA", "botId": 1, "timestamp": "2026-10-10T11:00:00", "profitLoss": 100.0}), # line 1546
        MagicMock(to_dict=lambda: {"symbol": "NVDA", "botId": 1, "timestamp": "2026-10-10T12:00:00", "profitLoss": -50.0}), # line 1582
        MagicMock(to_dict=lambda: {"symbol": "NVDA", "botId": 1, "timestamp": "2026-10-10T13:00:00", "profitLoss": 200.0}), # line 1579
    ]
    mock_metric_q = MagicMock()
    mock_metric_q.order_by.return_value.limit.return_value.stream.return_value = stat_trades
    with patch.object(dashboard_data.trades_ref, "where", return_value=mock_metric_q):
        res_stats = client.get("/api/bot-metrics/1")
        assert res_stats.status_code == 200
        assert res_stats.json()["total_trades"] == 3

    # Line 1646: Export trade history with TEST trade
    export_trades = [
        MagicMock(to_dict=lambda: {"symbol": "TEST", "botName": "TEST", "timestamp": "2026-10-10T10:00:00Z"}),
        MagicMock(to_dict=lambda: {"symbol": "NVDA", "botName": "nvda", "timestamp": "2026-10-10T11:00:00Z", "profitLoss": 50.0}),
    ]
    with patch.object(dashboard_data.trades_ref, "order_by") as mock_eob:
        mock_eob.return_value.stream.return_value = export_trades
        res_exp = client.get("/api/trade-history/export?start_month=2026-10&end_month=2026-10")
        assert res_exp.status_code == 200

    # Line 1734: Direct call to deprecated get_pnl_statement()
    with patch.object(dashboard_data, "calculate_pnl", return_value={"pnl": 0.0}):
        res_dep = asyncio.run(get_pnl_statement())
        assert res_dep == {"pnl": 0.0}

    # Line 1739: Direct call to health_check()
    hc = asyncio.run(health_check())
    assert hc["status"] == "healthy"

    # Line 1809: Logs update missing botId
    res_bad_log = client.post("/api/logs/update", json={"logs": ["line1"]})
    assert res_bad_log.status_code == 500

    # Lines 1927-1928, 1956-1957, 1993-1994: VM bot control non-JSON errors
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_bad_resp = MagicMock(is_success=False, status_code=502, text="502 Bad Gateway")
        mock_bad_resp.json.side_effect = Exception("not json")
        mock_post.return_value = mock_bad_resp
        assert client.post("/api/bot-control/stop").status_code == 502
        assert client.post("/api/bot-control/restart").status_code == 502

        mock_post.side_effect = Exception("reset unknown err")
        assert client.post("/api/bot-control/reset/nvda").status_code == 500

    # Line 2305 & 2344-2347: Switch profile branches
    mock_db_sp = MagicMock()
    mock_no_doc = MagicMock(exists=False)
    mock_db_sp.collection("bot_overview").document.return_value.get.return_value = mock_no_doc
    with patch("google.cloud.firestore.Client", return_value=mock_db_sp), \
         patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"status": "success", "profile_confirmed": True})
        res_sp = client.post("/api/bot-control/switch-profile", json={"profile": "paper"})
        assert res_sp.status_code == 200

        mock_post.side_effect = RuntimeError("unexpected switch boom")
        res_sp_err = client.post("/api/bot-control/switch-profile", json={"profile": "paper"})
        assert res_sp_err.status_code == 500

    # Lines 2441-2442 & 2620-2621: FileNotFoundError in learning_review_page & governance_review_page
    from main import learning_review_page, governance_review_page
    with patch("main.FileResponse", side_effect=FileNotFoundError("not found")):
        with pytest.raises(main.HTTPException):
            asyncio.run(learning_review_page())
        with pytest.raises(main.HTTPException):
            asyncio.run(governance_review_page())

    # Lines 2586-2587: Rejected status in learning stats
    mock_learn_doc = MagicMock(to_dict=lambda: {"status": "rejected", "priority": "high", "type": "modify_rule"})
    with patch("google.cloud.firestore.Client") as mock_fs:
        mock_fs.return_value.collection("learning_candidates").stream.return_value = [mock_learn_doc]
        res_lstats = client.get("/api/learning-candidates/stats")
        assert res_lstats.status_code == 200
        assert res_lstats.json()["total_rejected"] == 1

    # Line 2645: Exit governance with auto_apply_governance in bot params
    mock_gov_doc = MagicMock(id="NVDA", to_dict=lambda: {"symbol": "NVDA"})
    with patch("google.cloud.firestore.Client") as mock_fs, \
         patch("main.load_bot_parameters", return_value={"auto_apply_governance": False}):
        mock_fs.return_value.collection("exit_governance").stream.return_value = [mock_gov_doc]
        res_gov = client.get("/api/exit-governance")
        assert res_gov.status_code == 200

    # Line 2769: Exit telemetry symbol filtering
    mock_telem_doc1 = MagicMock(id="t1", to_dict=lambda: {"symbol": "NVDA", "analytical_value": True})
    mock_telem_doc2 = MagicMock(id="t2", to_dict=lambda: {"symbol": "AAPL", "analytical_value": True})
    with patch("google.cloud.firestore.Client") as mock_fs, \
         patch("main._has_telemetry_analytical_value", return_value=True):
        mock_fs.return_value.collection.return_value.order_by.return_value.limit.return_value.stream.return_value = [mock_telem_doc1, mock_telem_doc2]
        res_telem = client.get("/api/exit-telemetry/snapshots?symbol=NVDA")
        assert res_telem.status_code == 200

    # Line 2827: Historical bars empty stored_bars
    mock_hb_doc = MagicMock(exists=True, to_dict=lambda: {"bars": []})
    with patch.object(dashboard_data.db, "collection") as mock_coll:
        mock_coll.return_value.document.return_value.get.return_value = mock_hb_doc
        res_hb = client.get("/api/historical-bars/NVDA")
        assert res_hb.json() == []

    # Lines 2912, 3018, 3240, 3312, 3656, 3707, 3732, 3784, 3811, 3838: Unauthenticated endpoints
    unauth = TestClient(app)
    assert unauth.get("/api/stock-picks").status_code == 401
    assert unauth.post("/api/stock-picks/run").status_code == 401
    assert unauth.post("/api/stock-picks/reject", json={"symbol": "NVDA"}).status_code == 401
    assert unauth.get("/api/stock-picks/parameter-advice?symbol=NVDA").status_code == 401
    assert unauth.get("/api/stock-picks/feedback/status").status_code == 401
    assert unauth.post("/api/stock-picks/feedback/run-audit").status_code == 401
    assert unauth.post("/api/stock-picks/feedback/execution", json={"pick_id": "p1"}).status_code == 401
    assert unauth.get("/api/stock-picks/sentinel/status").status_code == 401
    assert unauth.post("/api/stock-picks/sentinel/audit").status_code == 401
    assert unauth.post("/api/stock-picks/sentinel/toggle", json={"enabled": True}).status_code == 401

    # Lines 2946-2950 & 2960-2975: Stock picks cache and empty state
    with _stock_picks_cache['lock']:
        _stock_picks_cache['data'] = None
        _stock_picks_cache['timestamp'] = None
    mock_picks_doc = MagicMock(exists=True, to_dict=lambda: {"picks": [], "run_timestamp": datetime.now()})
    with patch.object(dashboard_data.stock_picks_ref, "document", return_value=MagicMock(get=lambda: mock_picks_doc)):
        res_picks = client.get("/api/stock-picks?_t=1")
        assert res_picks.status_code == 200

    # Empty picks doc
    with _stock_picks_cache['lock']:
        _stock_picks_cache['data'] = None
        _stock_picks_cache['timestamp'] = None
    mock_empty_picks = MagicMock(exists=False)
    with patch.object(dashboard_data.stock_picks_ref, "document", return_value=MagicMock(get=lambda: mock_empty_picks)):
        res_empty_p = client.get("/api/stock-picks?_t=1")
        assert res_empty_p.status_code == 200
        assert res_empty_p.json()["status"] == "empty"

    # Lines 3227-3229 & 3295-3297: Accept and reject exceptions
    with patch.object(dashboard_data.stock_picks_ref, "document", side_effect=Exception("accept boom")):
        res_acc_err = client.post("/api/stock-picks/accept", json={"symbol": "NVDA"})
        assert res_acc_err.status_code == 500

    with patch.object(dashboard_data.stock_picks_ref, "document", side_effect=Exception("reject boom")):
        res_rej_err = client.post("/api/stock-picks/reject", json={"symbol": "NVDA"})
        assert res_rej_err.status_code == 500

    # Line 3281: Update existing rejection in cooldown
    mock_rej_curr = MagicMock()
    mock_rej_curr.exists = True
    mock_rej_curr.to_dict.return_value = {
        "actionable_leads": [],
        "rejected_cooldown": [{"symbol": "NVDA", "rejection_reason": "OLD_REASON"}]
    }
    with patch.object(dashboard_data.stock_picks_ref, "document") as mock_sp_doc:
        mock_sp_doc.return_value.get.return_value = mock_rej_curr
        res_rej_update = client.post("/api/stock-picks/reject", json={"symbol": "NVDA", "reason": "NEW_REASON"})
        assert res_rej_update.status_code == 200

    # Lines 1433, 1438-1439: Trades filtering TEST trade and missing seqNum
    doc_test_trade = MagicMock(to_dict=lambda: {"symbol": "TEST", "profitLoss": 0})
    doc_no_seq = MagicMock(to_dict=lambda: {"symbol": "NVDA", "profitLoss": 100})
    mock_query = MagicMock()
    mock_query.stream.return_value = [doc_test_trade, doc_no_seq]
    with main._trade_history_cache['lock']:
        main._trade_history_cache['data'] = None
        main._trade_history_cache['timestamp'] = None
    with patch.object(dashboard_data.trades_ref, "where") as mock_tr_w:
        mock_tr_w.return_value.order_by.return_value.limit.return_value = mock_query
        res_trades = client.get("/api/trade-history/all?days=30")
        assert res_trades.status_code == 200

    # Lines 2912, 3161, 3312, 3382, 3656, 3784: Unauthenticated 401 checks
    unauth_c = TestClient(app)
    assert unauth_c.get("/api/stock-picks").status_code == 401
    assert unauth_c.post("/api/stock-picks/accept", json={"symbol": "NVDA"}).status_code == 401
    assert unauth_c.get("/api/stock-picks/parameter-advice?symbol=NVDA").status_code == 401
    assert unauth_c.post("/api/stock-picks/provision-bot", json={"symbol": "NVDA"}).status_code == 401
    assert unauth_c.get("/api/stock-picks/feedback/status").status_code == 401
    assert unauth_c.get("/api/stock-picks/sentinel/status").status_code == 401

    # Lines 2948-2950: string run_timestamp AttributeError
    mock_str_ts_doc = MagicMock(exists=True, to_dict=lambda: {"run_timestamp": "2026-10-10T12:00:00Z"})
    with patch.object(dashboard_data.stock_picks_ref, "document") as mock_sp_ts:
        mock_sp_ts.return_value.get.return_value = mock_str_ts_doc
        with main._stock_picks_cache['lock']:
            main._stock_picks_cache['data'] = None
            main._stock_picks_cache['timestamp'] = None
        res_str_ts = client.get("/api/stock-picks?_t=1")
        assert res_str_ts.status_code == 200

    # Line 3197: not found in actionable leads
    mock_leads_doc = MagicMock(exists=True, to_dict=lambda: {"actionable_leads": [], "already_accepted": []})
    with patch.object(dashboard_data.stock_picks_ref, "document") as mock_acc_doc:
        mock_acc_doc.return_value.get.return_value = mock_leads_doc
        res_acc_notfound = client.post("/api/stock-picks/accept", json={"symbol": "NOTFOUND"})
        assert res_acc_notfound.status_code == 200

    # Line 3209: exists_idx is not None
    mock_leads_exist = MagicMock(exists=True, to_dict=lambda: {"already_accepted": [{"symbol": "EXISTS"}]})
    with patch.object(dashboard_data.stock_picks_ref, "document") as mock_acc_doc:
        mock_acc_doc.return_value.get.return_value = mock_leads_exist
        res_acc_exist = client.post("/api/stock-picks/accept", json={"symbol": "EXISTS"})
        assert res_acc_exist.status_code == 200

    # Lines 3348-3349: parameter-advice exception reading Firestore
    with patch.object(dashboard_data.stock_picks_ref, "document") as mock_pa_doc:
        mock_pa_doc.return_value.get.side_effect = Exception("firestore err")
        with main._stock_picks_cache['lock']:
            main._stock_picks_cache['data'] = None
            main._stock_picks_cache['timestamp'] = None
        res_pa = client.get("/api/stock-picks/parameter-advice?symbol=NVDA")
        assert res_pa.status_code == 200

    # Lines 3484, 3490, 3492, 3498, 3500, 3517, 3522-3529, 3544-3545: Provision custom params and lead update
    prov_payload = {
        "symbol": "NVDA",
        "strategy_track": "GROWTH",
        "custom_params": {
            "capital_per_bucket_long": 3000,
            "trailing_stop_atr_multiplier": 2.5,
            "position_min_shares": 10,
            "cc_mode": "OTM",
            "call_expiration_days": 45
        }
    }
    mock_curr_prov = MagicMock()
    mock_curr_prov.exists = True
    mock_curr_prov.to_dict.return_value = {
        "actionable_leads": [{"ticker": "NVDA"}],
        "already_accepted": [{"symbol": "NVDA", "status": "ACCEPTED"}]
    }
    with patch.object(dashboard_data.stock_picks_ref, "document") as mock_prov_doc, \
         patch("builtins.open", mock_open(read_data=json.dumps({"bots": []}))), \
         patch("os.makedirs"), \
         patch("httpx.AsyncClient.post", return_value=MagicMock(status_code=200)):
        mock_prov_doc.return_value.get.return_value = mock_curr_prov
        mock_prov_doc.return_value.set.side_effect = Exception("update status err")
        res_prov = client.post("/api/stock-picks/provision-bot", json=prov_payload)
        assert res_prov.status_code == 200

    # Lines 3622-3627 & 3633-3634: Provision failure handling already_accepted item
    mock_fail_curr = MagicMock()
    mock_fail_curr.exists = True
    mock_fail_curr.to_dict.return_value = {"already_accepted": [{"symbol": "FAIL"}]}
    try:
        with patch.object(dashboard_data.stock_picks_ref, "document") as mock_fail_doc, \
             patch("builtins.open", mock_open(read_data=json.dumps({"bots": []}))), \
             patch("os.path.exists", return_value=False):
            mock_fail_doc.return_value.get.return_value = mock_fail_curr
            mock_fail_doc.return_value.set.side_effect = Exception("fail write boom")
            res_prov_fail = client.post("/api/stock-picks/provision-bot", json={"symbol": "FAIL", "strategy_track": "GROWTH"})
            assert res_prov_fail.status_code == 500
    finally:
        for p in ["config/bots/fail.json", "../tradingbot-config/bots/fail.json"]:
            if os.path.exists(p):
                os.remove(p)

    # Lines 3671, 3675, 3680-3681: Feedback status doc exists and db=None
    mock_fl_doc = MagicMock(exists=True, to_dict=lambda: {"ledger": 1})
    mock_rej_doc = MagicMock(exists=True, to_dict=lambda: {"rej": 1})
    with patch.object(dashboard_data.db, "collection") as mock_c:
        mock_c.return_value.document.return_value.get.side_effect = [mock_fl_doc, mock_rej_doc]
        with patch("stockpicker.feedback_engine.compute_execution_summary", return_value={}), \
             patch("stockpicker.feedback_engine.load_weight_adjustments", return_value={}):
            res_fb_st = client.get("/api/stock-picks/feedback/status")
            assert res_fb_st.status_code == 200

    with patch.object(dashboard_data, "db", None):
        res_fb_nodb = client.get("/api/stock-picks/feedback/status")
        assert res_fb_nodb.status_code == 200

    # Lines 3713, 3718, 3736-3737: Audit and execution feedback errors
    with patch.object(dashboard_data, "db", None):
        res_audit_nodb = client.post("/api/stock-picks/feedback/run-audit")
        assert res_audit_nodb.status_code == 500

    res_exec_bad_json = client.post("/api/stock-picks/feedback/execution", content="not json", headers={"Content-Type": "application/json"})
    assert res_exec_bad_json.status_code == 400
