"""
Dedicated targeted test suite for Issue #95 to achieve >= 98.0% branch and statement coverage
across all dashboard modules and unmask CI test exit statuses.

Covers:
- stockpicker/alternative_data_client.py
- stockpicker/sentinel.py
- stockpicker/income_screener.py
- services/pnl_statement_engine.py
- stockpicker/core.py
- stockpicker/runner.py
- stockpicker/feedback_engine.py
- main.py
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, mock_open
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main
from main import (
    app,
    dashboard_data,
    authenticated_sessions,
)


@pytest.fixture
def auth_client():
    client = TestClient(app)
    session_id = "test-cov-session-issue95"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=4)
    client.cookies.set("dashboard_session", session_id)
    yield client, session_id
    authenticated_sessions.pop(session_id, None)


# ============================================================================
# 1. STOCKPICKER/ALTERNATIVE_DATA_CLIENT.PY
# ============================================================================

def test_alt_data_client_issue95_branches():
    from stockpicker.alternative_data_client import (
        _iso_date,
        _fetch_stockwatcher_feed,
    )

    # Empty date string or None
    assert _iso_date(None) == ""
    assert _iso_date("") == ""
    # Invalid date string that cannot be parsed by any known format
    assert _iso_date("not-a-valid-date-string") == "not-a-valid-date-string"

    # _fetch_stockwatcher_feed: response json is not a list
    mock_resp_dict = MagicMock(status_code=200)
    mock_resp_dict.json.return_value = {"error": "rate limit"}
    with patch("requests.get", return_value=mock_resp_dict):
        assert _fetch_stockwatcher_feed("https://example.com/feed", "House") is None

    # _fetch_stockwatcher_feed: list contains non-dict rows
    mock_resp_rows = MagicMock(status_code=200)
    mock_resp_rows.json.return_value = [
        "not-a-dict",
        12345,
        {"ticker": "NVDA", "representative": "Rep Test", "transaction_date": "2026-01-01"},
    ]
    with patch("requests.get", return_value=mock_resp_rows):
        res = _fetch_stockwatcher_feed("https://example.com/feed", "House")
        assert len(res) == 1
        assert res[0]["ticker"] == "NVDA"


# ============================================================================
# 2. STOCKPICKER/SENTINEL.PY
# ============================================================================

def test_sentinel_issue95_branches():
    from stockpicker.sentinel import (
        _persist_bot_telemetry,
        _persist_current_telemetry,
    )

    mock_db = MagicMock()

    # client_id is None -> returns early
    _persist_bot_telemetry(mock_db, pos_result={"symbol": "NVDA", "client_id": None, "thesis_telemetry": {}})
    mock_db.collection.assert_not_called()

    # Empty pos_results -> returns early
    _persist_current_telemetry(mock_db, [])

    # pos_results with empty symbol -> updates_by_symbol empty -> returns early
    _persist_current_telemetry(mock_db, [{"symbol": "", "thesis_telemetry": {}}])

    # mutate function when already_accepted is not a list (e.g. None or string)
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    with patch("stockpicker.firestore_utils.run_stock_pick_rmw") as mock_rmw:
        # Simulate mutate callback execution
        def fake_rmw(doc_ref, mutate_fn, db):
            mutate_fn({"already_accepted": "not-a-list"})
            return True

        mock_rmw.side_effect = fake_rmw
        pos_results = [{
            "symbol": "NVDA",
            "thesis_telemetry": {
                "score_delta": 2.5,
                "current_score": 85.0,
                "thesis_state": "HEALTHY",
                "action_instruction": "HOLD",
                "last_evaluated": "2026-11-04T10:00:00Z",
            }
        }]
        _persist_current_telemetry(mock_db, pos_results)
        mock_rmw.assert_called_once()


# ============================================================================
# 3. STOCKPICKER/INCOME_SCREENER.PY
# ============================================================================

def test_income_screener_issue95_branches():
    from stockpicker.income_screener import evaluate_covered_call_candidate

    mock_options = {
        "quote": {"regularMarketPrice": 100.0, "bid": 100.0},
        "options": [{
            "expirationDate": "invalid",
            "calls": [{
                "strike": 105.0,
                "bid": 2.0,
                "ask": 2.2,
                "expiration": "not-a-timestamp",
            }]
        }]
    }

    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=mock_options):
        metrics = evaluate_covered_call_candidate("AAPL")
        assert metrics is not None
        assert "monthly_yield_est" in metrics


# ============================================================================
# 4. SERVICES/PNL_STATEMENT_ENGINE.PY
# ============================================================================

def test_pnl_statement_engine_issue95_branches():
    from services.pnl_statement_engine import aggregate_portfolio_pnl

    configs = [{"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "trading_mode": "paper"}]
    bot_overview = {"nvda": {"symbol": "NVDA", "position_size": 10, "current_price": 100.0, "trading_mode": "paper"}}
    # Trade with invalid unparseable timestamp hits line 669 (if not ts: continue)
    trades = [
        {"timestamp": "totally-invalid-timestamp-string", "symbol": "NVDA", "botId": 4, "profitLoss": 50.0},
        {"timestamp": "2026-11-01T12:00:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 10.0},
    ]

    res = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="paper", period="ALL")
    assert res is not None
    assert len(res.calendar_heatmap) >= 1


# ============================================================================
# 5. STOCKPICKER/CORE.PY
# ============================================================================

def test_stockpicker_core_issue95_branches(monkeypatch):
    import stockpicker.core as core

    # 1. sec_headers with example.com in SEC_USER_AGENT
    monkeypatch.setenv("SEC_USER_AGENT", "bot@example.com")
    hdrs = core.sec_headers()
    assert "internal.local" in hdrs["User-Agent"]

    # 2. fetch_finnhub_news error branches
    monkeypatch.setenv("FINNHUB_API_KEY", "mock_key")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [
        # invalid pub_unix string
        {"headline": "H1", "summary": "S1", "datetime": "not-a-number", "url": "u1"},
        # empty headline and summary
        {"headline": "", "summary": "", "datetime": 1700000000, "url": "u2"},
    ]
    with patch("requests.get", return_value=mock_resp):
        items = core.fetch_finnhub_news(since_iso="2026-11-01T00:00:00Z")
        assert len(items) == 1

    # requests.get raises Exception
    with patch("requests.get", side_effect=Exception("Finnhub network down")):
        assert core.fetch_finnhub_news(since_iso="2026-11-01T00:00:00Z") == []

    # 3. get_finnhub_insider_trades without API key
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    insider = core.get_finnhub_insider_trades("AAPL")
    assert insider["insider_score"] == 0.0
    monkeypatch.setenv("FINNHUB_API_KEY", "mock_key")

    # 4. fetch_alphavantage_news branches
    # Rate limit Information note
    with patch("stockpicker.core.safe_get", return_value={"Information": "Rate limit exceeded"}):
        assert core.fetch_alphavantage_news("2026-11-01T00:00:00Z") == []

    # Article with unparseable time_published and non-float sentiment score
    feed_data = {
        "feed": [
            {
                "time_published": "20261399T999999",  # unparseable time
                "headline": "AV Headline",
                "summary": "AV Summary",
                "overall_sentiment_score": "not-a-float",
                "ticker_sentiment": [{"ticker": "NVDA"}],
            }
        ]
    }
    with patch("stockpicker.core.safe_get", return_value=feed_data):
        av_items = core.fetch_alphavantage_news("2026-11-01T00:00:00Z")
        assert len(av_items) == 1

    # 5. fetch_all_news: empty primary sources and duplicate headlines
    item1 = core.NewsItem(
        source_type="finnhub",
        source_name="Finnhub",
        published_at="2026-11-01T12:00:00Z",
        headline="Same Headline",
        summary="Sum",
        url="http",
        engagement=0.0,
        tickers="NVDA",
        raw_json="{}",
    )
    item2 = core.NewsItem(
        source_type="finnhub",
        source_name="Finnhub",
        published_at="2026-11-01T12:00:00Z",
        headline="Same Headline",  # duplicate
        summary="Sum 2",
        url="http",
        engagement=0.0,
        tickers="NVDA",
        raw_json="{}",
    )
    with patch("stockpicker.core.fetch_newsapi", return_value=[]), \
         patch("stockpicker.core.fetch_x_recent", return_value=[]), \
         patch("stockpicker.core.fetch_polygon_benzinga", return_value=[]), \
         patch("stockpicker.core.fetch_finnhub_news", return_value=[item1, item2]):
        monkeypatch.setenv("FINNHUB_API_KEY", "mock_key")
        news = core.fetch_all_news(hours=24)
        assert len(news) == 1

    # Primary empty + Alpha Vantage key set
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av_mock_key")
    with patch("stockpicker.core.fetch_newsapi", return_value=[]), \
         patch("stockpicker.core.fetch_x_recent", return_value=[]), \
         patch("stockpicker.core.fetch_polygon_benzinga", return_value=[]), \
         patch("stockpicker.core.fetch_finnhub_news", return_value=[]), \
         patch("stockpicker.core.fetch_alphavantage_news", return_value=[item1]):
        news_av = core.fetch_all_news(hours=24)
        assert len(news_av) == 1

    # 6. get_companyfacts_quarterly exception
    with patch("stockpicker.core.get_sec_ticker_map", return_value={"NVDA": "0001045810"}), \
         patch("stockpicker.core.rate_limited_get", side_effect=Exception("sec fail")):
        assert core.get_companyfacts_quarterly("NVDA") == {}

    # 7. get_yahoo_crumb_and_cookies
    # Session path
    mock_session = MagicMock()
    mock_session.get.side_effect = [
        MagicMock(status_code=200),
        MagicMock(status_code=200, text="valid_session_crumb"),
    ]
    mock_session.cookies = {"B": "test_cookie"}
    with patch("requests.Session", return_value=mock_session):
        crumb, cookies = core.get_yahoo_crumb_and_cookies(force_refresh=True)
        assert crumb == "valid_session_crumb"

    # fc.yahoo fallback path
    mock_session_fail = MagicMock()
    mock_session_fail.get.side_effect = Exception("session down")
    mock_fc_resp = MagicMock(status_code=200, cookies={"A": "fc_cookie"})
    mock_crumb_resp = MagicMock(status_code=200, text="valid_fc_crumb")
    with patch("requests.Session", return_value=mock_session_fail), \
         patch("requests.get", side_effect=[mock_fc_resp, mock_crumb_resp]):
        crumb, cookies = core.get_yahoo_crumb_and_cookies(force_refresh=True)
        assert crumb == "valid_fc_crumb"

    # 8. compute_fundamental_score scoring branches
    sec_data = {
        "revenue": [{"val": 100}],
        "net_income": [{"val": 10}],
        "operating_income": [{"val": 10}],
        "cash": [{"val": 100}],
    }
    yf_data = {
        "price": {
            "regularMarketPrice": 105.0,
            "regularMarketPreviousClose": 100.0,
            "regularMarketVolume": 20000,
            "averageVolume": 100000,  # rel_vol <= 0.5
        },
        "financialData": {
            "debtToEquity": 50,
            "currentRatio": 1.5,
            "grossMargins": 0.4,
            "operatingMargins": 0.2,
            "recommendationMean": 4.0,  # Sell recommendation >= 3.5
        },
        "earningsHistory": {"history": [{"surprisePercent": {"raw": 0.15}}]},
    }
    core._fundamental_scores_cache.clear()
    with patch("stockpicker.core.get_companyfacts_quarterly", return_value=sec_data), \
         patch("stockpicker.core.get_yahoo_financial_snapshot", return_value=yf_data), \
         patch("stockpicker.core.get_alpha_earnings", return_value=None), \
         patch("stockpicker.core.get_finnhub_insider_trades", return_value={"insider_score": 0.0, "reason": "no insider"}), \
         patch("stockpicker.alternative_data_client.compute_congressional_trading_score", return_value={"congress_score": 0.0, "trade_count": 0, "reason": "no congress"}):
        res_fund = core.compute_fundamental_score("NVDA")
        assert "score" in res_fund
        assert "rel_vol=" in res_fund["fundamental_reasons"]

    # 9. build_evidence_dossier INCOME track ETF vs non-ETF
    dossier_etf = core.build_evidence_dossier("JEPI", "Finance", "Yield", {}, strategy_track="INCOME", options_data=None)
    assert "Options Chain Greeks" in dossier_etf["missing_sources"]

    dossier_stock = core.build_evidence_dossier(
        "AAPL",
        "Tech",
        "Growth",
        {},
        strategy_track="INCOME",
        options_data={"monthly_yield_est": 0.03}
    )
    assert "Earnings Calendar" in dossier_stock["missing_sources"]

    # 10. get_finnhub_insider_trades moderate buy and sell branches
    monkeypatch.setenv("FINNHUB_API_KEY", "mock_key")
    with patch("stockpicker.core.safe_get", return_value={"data": [{"change": 15000, "transactionPrice": 1.0, "transactionCode": "P", "name": "Buyer"}]}):
        r_buy = core.get_finnhub_insider_trades("NVDA")
        assert "insider_buying" in r_buy["reason"]

    with patch("stockpicker.core.safe_get", return_value={"data": [{"change": -150000, "transactionPrice": 1.0, "transactionCode": "S", "name": "Seller"}]}):
        r_sell = core.get_finnhub_insider_trades("NVDA")
        assert "insider_selling" in r_sell["reason"]


# ============================================================================
# 6. STOCKPICKER/RUNNER.PY
# ============================================================================

def test_stockpicker_runner_issue95_branches():
    import stockpicker.runner as runner

    mock_db = MagicMock()

    # 1. load_review_state when to_dict() returns None or non-dict
    mock_doc_none = MagicMock(exists=True)
    mock_doc_none.to_dict.return_value = None
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc_none
    state = runner.load_review_state(mock_db)
    assert state == {}

    # load_review_state with valid REVIEW_STATE_KEYS and reviewed picks
    mock_doc_valid = MagicMock(exists=True)
    mock_doc_valid.to_dict.return_value = {
        "already_accepted": [{"symbol": "NVDA", "status": "ACCEPTED"}],
        "picks": [
            {"symbol": "NVDA", "status": "PROVISIONED"},
            {"symbol": "AAPL", "status": "PENDING"},  # not reviewed
        ]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc_valid
    state_valid = runner.load_review_state(mock_db)
    assert len(state_valid["already_accepted"]) == 1
    assert len(state_valid["picks"]) == 1

    # 2. _parse_iso_dt naive datetime and exception
    dt_naive = runner._parse_iso_dt("2026-11-04T10:00:00")
    assert dt_naive is not None
    assert dt_naive.tzinfo is not None

    assert runner._parse_iso_dt("not-an-iso-dt") is None

    # 3. _rejection_still_in_cooldown branches
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    # Cooldown expires in future
    assert runner._rejection_still_in_cooldown(
        {"cooldown_expires": (now + timedelta(days=1)).isoformat()}, cutoff
    ) is True
    # Cooldown expires in past (< cutoff)
    assert runner._rejection_still_in_cooldown(
        {"cooldown_expires": (now - timedelta(days=10)).isoformat()}, cutoff
    ) is False
    # No cooldown_expires, rejected_at is None -> assumed still cooling (line 306)
    assert runner._rejection_still_in_cooldown({}, cutoff) is True
    # rejected_at < cutoff -> expired
    assert runner._rejection_still_in_cooldown(
        {"rejected_at": (now - timedelta(days=10)).isoformat()}, cutoff
    ) is False

    # 4. merge_review_state
    # current is None and db is None
    payload = {"already_accepted": [], "rejected_cooldown": []}
    assert runner.merge_review_state(None, payload, current=None) == payload

    # current is None and db get raises Exception
    mock_db_err = MagicMock()
    mock_db_err.collection.return_value.document.return_value.get.side_effect = Exception("fs read error")
    assert runner.merge_review_state(mock_db_err, payload, current=None) == payload

    # Items in current with non-dict items and accepted_at / rejected_at >= tolerance
    current_data = {
        "already_accepted": [
            "not-a-dict",
            {"symbol": "MSFT", "accepted_at": (now + timedelta(minutes=1)).isoformat()}
        ],
        "rejected_cooldown": [
            "not-a-dict",
            {"symbol": "GOOG", "rejected_at": (now + timedelta(minutes=1)).isoformat()}
        ]
    }
    merged = runner.merge_review_state(None, payload, scan_started_at=now, current=current_data)
    assert any(x.get("symbol") == "MSFT" for x in merged["already_accepted"])
    assert any(x.get("symbol") == "GOOG" for x in merged["rejected_cooldown"])


# ============================================================================
# 7. STOCKPICKER/FEEDBACK_ENGINE.PY
# ============================================================================

def test_stockpicker_feedback_engine_issue95_branches():
    import stockpicker.feedback_engine as fe

    mock_db = MagicMock()

    # 1. archive_run_snapshots: existing doc has protected fields that get copied
    mock_existing_doc = MagicMock(exists=True)
    mock_existing_doc.to_dict.return_value = {
        "realized_pnl": 150.0,
        "holding_days": 12,
        "exit_date": "2026-11-01",
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_existing_doc
    pick = {
        "ticker": "NVDA",
        "composite_score": 90.0,
        "entry_price": 100.0,
    }
    fe.archive_run_snapshots(mock_db, [pick], run_date="2026-11-04T10:00:00Z")

    # existing doc get raises Exception
    mock_db.collection.return_value.document.return_value.get.side_effect = Exception("fs err")
    fe.archive_run_snapshots(mock_db, [pick], run_date="2026-11-04T10:00:00Z")

    # 2. compute_forward_alpha error branches
    mock_doc = MagicMock()
    mock_doc.id = "snap1"
    run_date_str = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    mock_doc.to_dict.return_value = {
        "ticker": "NVDA",
        "entry_price": 100.0,
        "run_date": run_date_str,
    }
    mock_db.collection.return_value.where.return_value.stream.return_value = [mock_doc]

    # SPY historical price returns None
    with patch("stockpicker.feedback_engine._fetch_historical_price", return_value=None):
        sum_spy_none = fe.compute_forward_alpha(mock_db)
        assert sum_spy_none["alphas_updated"] == 0

    # Historical price fetch raises Exception
    with patch("stockpicker.feedback_engine._fetch_historical_price", side_effect=[100.0, Exception("price err")]):
        sum_err = fe.compute_forward_alpha(mock_db)
        assert len(sum_err["errors"]) > 0

    # 3. compute_execution_summary excessive stop outs flag (> 0.60 and >= 3 samples)
    mock_rec1 = MagicMock(to_dict=lambda: {"sector": "Technology", "exit_reason": "stop_loss", "realized_pnl": -10.0})
    mock_rec2 = MagicMock(to_dict=lambda: {"sector": "Technology", "exit_reason": "stop_loss", "realized_pnl": -10.0})
    mock_rec3 = MagicMock(to_dict=lambda: {"sector": "Technology", "exit_reason": "stop_loss", "realized_pnl": -10.0})
    mock_rec4 = MagicMock(to_dict=lambda: {"sector": "Technology", "exit_reason": "take_profit", "realized_pnl": 50.0})
    mock_db.collection.return_value.where.return_value.stream.return_value = [mock_rec1, mock_rec2, mock_rec3, mock_rec4]
    summary = fe.compute_execution_summary(mock_db)
    assert any(f.get("flag") == "EXCESSIVE_STOP_OUTS" for f in summary["self_healing_flags"])

    # 4. cleanup_stale_archives deletion error branches
    mock_old_doc = MagicMock()
    mock_old_doc.id = "old1"
    mock_old_doc.reference.delete.side_effect = Exception("delete boom")
    mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = [mock_old_doc]
    purged = fe.cleanup_stale_archives(mock_db, ttl_days=90)
    assert len(purged["deleted"]["errors"]) > 0

    # 5. _fetch_historical_price returns close price
    import pandas as pd
    mock_df = pd.DataFrame({"Close": [150.0]}, index=pd.date_range("2026-11-01", periods=1))
    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = mock_df
        p = fe._fetch_historical_price("NVDA", "2026-11-01")
        assert p == 150.0


# ============================================================================
# 8. MAIN.PY
# ============================================================================

def test_main_unauthenticated_stockpicker_and_sentinel():
    """Verify that every stockpicker and sentinel endpoint raises 401 when unauthenticated."""
    unauth_client = TestClient(app)

    endpoints = [
        ("GET", "/api/stock-picks/candidates"),
        ("POST", "/api/stock-picks/run"),
        ("POST", "/api/stock-picks/accept"),
        ("POST", "/api/stock-picks/reject"),
        ("POST", "/api/stock-picks/reset-cooldown"),
        ("GET", "/api/stock-picks/candidate-detail?symbol=AAPL"),
        ("POST", "/api/stock-picks/provision-bot"),
        ("GET", "/api/stock-picks/feedback/status"),
        ("POST", "/api/stock-picks/feedback/run-audit"),
        ("POST", "/api/stock-picks/feedback/execution"),
        ("GET", "/api/stock-picks/sentinel/status"),
        ("POST", "/api/stock-picks/sentinel/audit"),
        ("POST", "/api/stock-picks/sentinel/toggle"),
    ]

    for method, path in endpoints:
        if method == "GET":
            resp = unauth_client.get(path)
        else:
            resp = unauth_client.post(path, json={})
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code} instead of 401"


def test_main_datetime_and_session_branches():
    from main import _as_naive_local, is_valid_session

    # _as_naive_local
    assert _as_naive_local(None) is None
    assert _as_naive_local("invalid-string") is None
    assert _as_naive_local(12345) is None
    dt_aware = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    dt_norm = _as_naive_local(dt_aware)
    assert dt_norm.tzinfo is None

    # is_valid_session
    # 1. empty session id
    assert is_valid_session("") is False

    # 2. cache miss with sessions_ref=None
    with patch("main.sessions_ref", None):
        assert is_valid_session("unseen-id") is False

    # 3. cache miss with Firestore query
    mock_sessions_ref = MagicMock()

    # Doc does not exist
    mock_not_found = MagicMock(exists=False)
    mock_sessions_ref.document.return_value.get.return_value = mock_not_found
    with patch("main.sessions_ref", mock_sessions_ref):
        assert is_valid_session("missing-id") is False

    # Doc query throws Exception
    mock_sessions_ref.document.return_value.get.side_effect = Exception("firestore error")
    with patch("main.sessions_ref", mock_sessions_ref):
        assert is_valid_session("error-id") is False

    # Doc exists, expiry is None
    mock_no_expiry = MagicMock(exists=True, to_dict=lambda: {"expiry": None})
    mock_sessions_ref.document.return_value.get.side_effect = None
    mock_sessions_ref.document.return_value.get.return_value = mock_no_expiry
    with patch("main.sessions_ref", mock_sessions_ref):
        assert is_valid_session("no-expiry-id") is False

    # Doc exists, expired in Firestore -> calls delete, returns False
    expired_time = (datetime.now() - timedelta(hours=1)).isoformat()
    mock_expired = MagicMock(exists=True, to_dict=lambda: {"expiry": expired_time})
    mock_sessions_ref.document.return_value.get.return_value = mock_expired
    with patch("main.sessions_ref", mock_sessions_ref):
        assert is_valid_session("expired-id") is False
        mock_sessions_ref.document.return_value.delete.assert_called()

    # Doc exists, valid in Firestore -> caches in authenticated_sessions, returns True
    valid_time = (datetime.now() + timedelta(hours=1)).isoformat()
    mock_valid = MagicMock(exists=True, to_dict=lambda: {"expiry": valid_time})
    mock_sessions_ref.document.return_value.get.return_value = mock_valid
    with patch("main.sessions_ref", mock_sessions_ref):
        assert is_valid_session("valid-remote-id") is True
        assert "valid-remote-id" in authenticated_sessions

    # 4. cache hit but expired locally -> purges and calls Firestore delete
    authenticated_sessions["expired-local"] = datetime.now() - timedelta(minutes=5)
    with patch("main.sessions_ref", mock_sessions_ref):
        assert is_valid_session("expired-local") is False
        assert "expired-local" not in authenticated_sessions


def test_main_bot_parameters_and_execution_metrics():
    # load_bot_parameters write failure and Firestore error
    with patch.object(dashboard_data, "db") as mock_db:
        mock_db.collection.return_value.document.return_value.get.side_effect = Exception("fs error")
        with patch("builtins.open", side_effect=Exception("write error")):
            res = main.load_bot_parameters("UNKNOWN_BOT")
            assert res is None

    # _load_execution_metrics stream fallbacks
    mock_metrics_ref = MagicMock()
    # order_by('ts') fails, order_by('timestamp') fails, falls back to limit().stream()
    mock_metrics_ref.order_by.side_effect = Exception("no index")
    mock_doc = MagicMock(exists=True)
    mock_doc.to_dict.return_value = {"ts": "2026-11-04T10:00:00Z", "slippage": 0.01}
    mock_metrics_ref.limit.return_value.stream.return_value = [mock_doc]

    with patch.object(dashboard_data, "execution_metrics_ref", mock_metrics_ref):
        metrics = dashboard_data._load_execution_metrics(limit=10)
        assert len(metrics) == 1

    # execution_metrics_ref is None or error
    with patch.object(dashboard_data, "execution_metrics_ref", None):
        assert dashboard_data._load_execution_metrics(limit=10) == []

    mock_err_ref = MagicMock()
    mock_err_ref.order_by.side_effect = Exception("err")
    mock_err_ref.limit.side_effect = Exception("err")
    with patch.object(dashboard_data, "execution_metrics_ref", mock_err_ref):
        assert dashboard_data._load_execution_metrics(limit=10) == []


def test_main_engine_performance_and_trade_deletion_fallbacks(auth_client):
    client, _ = auth_client

    # /api/engine-performance: acct_doc.get() raises Exception and _load_execution_metrics raises Exception
    with patch.object(dashboard_data.db, "collection") as mock_c:
        mock_c.return_value.document.return_value.get.side_effect = Exception("acct err")
        with patch.object(dashboard_data, "_load_execution_metrics", side_effect=Exception("exec err")), \
             patch("main.load_bot_configs", return_value={"bots": []}):
            res_perf = client.get("/api/engine-performance")
            assert res_perf.status_code == 200

    # /api/trade-history/delete fallbacks
    mock_trade_doc = MagicMock()
    mock_trade_doc.id = "doc123"
    mock_trade_doc.to_dict.return_value = {
        "timestamp": "2026-11-01T12:00:00Z",
        "entryOrderId": 12345,
    }
    mock_trades_ref = MagicMock()
    # 1. timestamp query -> empty
    # 2. entryOrderId int query -> empty
    # 3. entryOrderId str query -> [mock_trade_doc]
    mock_trades_ref.where.side_effect = [
        MagicMock(stream=MagicMock(return_value=[])),
        MagicMock(stream=MagicMock(return_value=[])),
        MagicMock(stream=MagicMock(return_value=[mock_trade_doc])),
    ]
    with patch.object(dashboard_data, "trades_ref", mock_trades_ref):
        res_del = client.delete(
            "/api/trade-history/delete?trade_id=TRADE-1&entry_order_id=12345"
        )
        assert res_del.status_code == 200

    # /api/bot-control/stop status update exception
    mock_bot_status_c = MagicMock()
    mock_sdoc = MagicMock()
    mock_sdoc.reference.update.side_effect = Exception("update status err")
    mock_bot_status_c.stream.return_value = [mock_sdoc]

    mock_async_stop = MagicMock()
    mock_stop_resp = MagicMock(is_success=True, status_code=200)
    mock_stop_resp.json.return_value = {"status": "success", "message": "Bots stopped"}
    mock_async_stop.__aenter__.return_value.post.return_value = mock_stop_resp

    with patch.object(dashboard_data.db, "collection", return_value=mock_bot_status_c), \
         patch("httpx.AsyncClient", return_value=mock_async_stop):
        res_stop = client.post("/api/bot-control/stop")
        assert res_stop.status_code == 200


def test_main_bot_control_proxy_and_profile_exceptions(auth_client):
    client, _ = auth_client

    # /api/bot-control/current-profile unexpected Exception -> 502
    mock_async_client_err = MagicMock()
    mock_async_client_err.__aenter__.return_value.get.side_effect = Exception("VM offline")
    with patch("httpx.AsyncClient", return_value=mock_async_client_err):
        res_prof = client.get("/api/bot-control/current-profile")
        assert res_prof.status_code == 502

    # /api/bot-control/bots/nvda_sma/enable unexpected Exception -> 502
    mock_async_proxy_err = MagicMock()
    mock_async_proxy_err.__aenter__.return_value.request.side_effect = Exception("VM timeout")
    with patch("httpx.AsyncClient", return_value=mock_async_proxy_err):
        res_proxy = client.post("/api/bot-control/bots/nvda_sma/enable")
        assert res_proxy.status_code == 502

    # proxy endpoint response error with non-JSON text
    mock_err_resp = MagicMock(is_error=True, status_code=500, text="Internal VM Error")
    mock_err_resp.json.side_effect = Exception("not json")
    mock_async_proxy_500 = MagicMock()
    mock_async_proxy_500.__aenter__.return_value.request.return_value = mock_err_resp
    with patch("httpx.AsyncClient", return_value=mock_async_proxy_500):
        res_proxy_err = client.post("/api/bot-control/bots/nvda_sma/enable")
        assert res_proxy_err.status_code == 500


def test_main_stockpicker_concurrency_and_provision_branches(auth_client):
    client, _ = auth_client

    # Stockpicker run opportunistic recovery when previous future is done
    fake_future = MagicMock()
    fake_future.done.return_value = True
    with patch("main._stockpicker_running", True), \
         patch("main._stockpicker_future", fake_future), \
         patch("stockpicker.runner.run_stockpicker", return_value={"status": "success"}):
        res_run = client.post("/api/stock-picks/run")
        assert res_run.status_code in (200, 409)

    # Provision custom_params call_expiration_days without covered_calls
    prov_payload = {
        "symbol": "AMD",
        "strategy_track": "GROWTH",
        "custom_params": {
            "call_expiration_days": 30
        }
    }
    try:
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "actionable_leads": [{"symbol": "AMD"}],
            "already_accepted": [],
        }
        with patch.object(dashboard_data.stock_picks_ref, "document") as mock_sp_doc, \
             patch("builtins.open", mock_open(read_data=json.dumps({"bots": []}))), \
             patch("os.makedirs"), \
             patch("httpx.AsyncClient.post", return_value=MagicMock(status_code=200)):
            mock_sp_doc.return_value.get.return_value = mock_doc
            res_prov = client.post("/api/stock-picks/provision-bot", json=prov_payload)
            assert res_prov.status_code == 200
    finally:
        for p in [
            "config/bots/amd.json",
            "../tradingbot-config/bots/amd.json",
            os.path.join(os.path.dirname(__file__), "..", "config", "bots", "amd.json"),
        ]:
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # Sentinel endpoints disabled and toggle
    with patch("stockpicker.sentinel.is_sentinel_enabled", return_value=False):
        res_tel = client.get("/api/stock-picks/sentinel/status")
        assert res_tel.status_code == 200
        assert res_tel.json()["status"] == "disabled"

        res_audit = client.post("/api/stock-picks/sentinel/audit")
        assert res_audit.status_code == 200
        assert res_audit.json()["status"] == "disabled"

    with patch("stockpicker.sentinel.set_sentinel_enabled"), \
         patch("stockpicker.sentinel.is_sentinel_enabled", return_value=True):
        res_toggle = client.post("/api/stock-picks/sentinel/toggle", json={"enabled": True})
        assert res_toggle.status_code == 200


def test_main_dashboard_data_initialization_branches():
    # 1. Success path
    mock_client = MagicMock()
    mock_doc = MagicMock(exists=True)
    mock_doc.id = "trade_doc_1"
    mock_doc.to_dict.return_value = {
        "analysis_metadata": {"trade_id": "T123"},
        "timestamp": "2026-11-04T10:00:00Z",
        "symbol": "AAPL",
        "profitLoss": 50.0,
    }
    mock_client.collection.return_value.document.return_value.get.return_value = MagicMock(
        exists=True, to_dict=lambda: {"data": {"bot1": {}}}
    )
    mock_client.collection.return_value.order_by.return_value.limit.return_value.stream.return_value = [mock_doc]
    mock_client.collection.return_value.stream.return_value = []

    with patch("main.firestore.Client", return_value=mock_client):
        dd = main.DashboardData()
        assert "bot1" in dd.bot_data
        assert len(dd.trades) == 1
        assert dd.trades[0]["id"] == "trade_doc_1"
        assert dd.trades[0]["trade_id"] == "T123"

    # 2. Failure path (offline / CI environment)
    with patch("main.firestore.Client", side_effect=Exception("fs offline")):
        dd_fail = main.DashboardData()
        assert dd_fail.bot_data == {}
        assert dd_fail.db is not None

