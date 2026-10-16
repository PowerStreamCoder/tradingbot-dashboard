"""
Targeted tests to bridge remaining coverage gaps across all tradingbot-dashboard modules to exceed 98%.
Covers:
- services/pnl_statement_engine.py
- stockpicker/runner.py
- stockpicker/alternative_data_client.py
- stockpicker/sentinel.py
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
from google.api_core import exceptions as gcp_exceptions

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main
from main import app, dashboard_data, load_bot_configs, authenticated_sessions


@pytest.fixture
def auth_client():
    client = TestClient(app)
    session_id = "test-cov-session-targeted"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=4)
    client.cookies.set("dashboard_session", session_id)
    yield client, session_id
    authenticated_sessions.pop(session_id, None)


# ============================================================================
# 1. SERVICES/PNL_STATEMENT_ENGINE.PY GAP FILLING
# ============================================================================

class TestPnLStatementEngineGaps:
    def test_datetime_and_calc_gaps(self):
        from services.pnl_statement_engine import (
            parse_iso_datetime,
            get_period_start_date,
            calculate_max_drawdown,
            aggregate_portfolio_pnl,
        )

        # 1. strptime fallback branch in parse_iso_datetime
        dt_fallback = parse_iso_datetime("2026-10-10T12:00:00 INVALID EXTRA")
        assert dt_fallback is not None
        assert dt_fallback.year == 2026

        # 2. get_period_start_date with now=None
        dt_now_none = get_period_start_date("1D", now=None)
        assert dt_now_none is not None

        # 3. calculate_max_drawdown with empty list
        assert calculate_max_drawdown([]) == 0.0

        # 4. aggregate_portfolio_pnl branches
        configs = [{"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "trading_mode": "live"}]
        bot_overview = {
            "nvda": {"symbol": "NVDA", "position_size": 10, "current_price": 100.0, "trading_mode": "live"},
            "bot1": {"symbol": "", "position_size": 0},
            "paper_pos": {"symbol": "NVDA", "trading_mode": "paper"},
        }
        trades = [
            {"timestamp": "2026-10-10T10:00:00Z", "symbol": "", "botId": 4, "profitLoss": 50.0},
            {"timestamp": "2026-10-10T11:00:00Z", "symbol": "UNKNOWN", "botId": 999, "profitLoss": 10.0},
            {"timestamp": "INVALID_TS", "symbol": "NVDA", "botId": 4, "profitLoss": 20.0},
        ]

        # Mode normalization: invalid mode -> all, both -> all
        res_both = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="both", period="1D")
        assert res_both.trading_mode == "all"

        res_inv = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="invalid_mode", period="1D")
        assert res_inv.trading_mode == "all"

        # bot_scope not in symbols_for_mode
        res_scope = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="paper", bot_scope="NVDA", period="1D")
        assert res_scope is not None

        # mode=paper with paper_pos filtering
        res_paper = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="paper", period="1D")
        assert res_paper is not None

        # mode=live with pos filtering
        res_live = aggregate_portfolio_pnl(trades, bot_overview, configs, mode="live", period="1D")
        assert res_live is not None


# ============================================================================
# 2. STOCKPICKER/RUNNER.PY GAP FILLING
# ============================================================================

class TestStockpickerRunnerGaps:
    def test_runner_branches(self):
        from stockpicker.runner import run_stockpicker

        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection("stock_picks").document("current").get.return_value = mock_doc

        # Test dynamic weights loaded branch (lines 270-272)
        with patch("google.cloud.firestore.Client", return_value=mock_db), \
             patch("stockpicker.feedback_engine.load_weight_adjustments", return_value={"momentum": 1.2}), \
             patch("stockpicker.core.fetch_all_news", return_value=[]), \
             patch("stockpicker.core.generate_fundamentals_picks", return_value=[{"ticker": "NVDA", "composite_score": 85.0}]), \
             patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
             patch("stockpicker.feedback_engine.archive_run_snapshots", side_effect=Exception("arc err")), \
             patch("stockpicker.feedback_engine.run_feedback_loop", side_effect=Exception("fb err")), \
             patch("stockpicker.runner.load_registered_bots", return_value={}):
            res = run_stockpicker()
            assert res is not None
            assert res.get("status") == "success"

        # Test weight adjustments exception branch (line 272)
        with patch("google.cloud.firestore.Client", return_value=mock_db), \
             patch("stockpicker.feedback_engine.load_weight_adjustments", side_effect=Exception("weight err")), \
             patch("stockpicker.core.fetch_all_news", return_value=[]), \
             patch("stockpicker.core.generate_fundamentals_picks", return_value=[]), \
             patch("stockpicker.income_screener.screen_income_candidates", return_value=[]), \
             patch("stockpicker.runner.load_registered_bots", return_value={}):
            res2 = run_stockpicker()
            assert res2 is not None


# ============================================================================
# 3. STOCKPICKER/ALTERNATIVE_DATA_CLIENT.PY GAP FILLING
# ============================================================================

class TestAlternativeDataGaps:
    def test_congressional_trades_bad_date(self):
        from stockpicker.alternative_data_client import compute_congressional_trading_score

        trades = [
            {"transaction_date": "INVALID_DATE_FORMAT", "amount": "$15,001 - $50,000", "representative": "Rep X"}
        ]
        score_res = compute_congressional_trading_score("AAPL", congress_trades=trades)
        assert score_res["congress_score"] >= 0.0


# ============================================================================
# 4. STOCKPICKER/SENTINEL.PY GAP FILLING
# ============================================================================

class TestStockpickerSentinelGaps:
    def test_sentinel_branches(self):
        from stockpicker.sentinel import (
            _hydrate_active_positions,
            _persist_telemetry,
            _find_bots_config,
        )

        # 1. _find_bots_config returning None (line 292)
        with patch("os.path.exists", return_value=False):
            assert _find_bots_config() is None

        # 2. _hydrate_active_positions with exception reading bots_config_path
        # and augmenting with previously unseen symbol in already_accepted
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "already_accepted": [
                {"symbol": "UNSEEN_SYM", "client_id": 99, "strategy_track": "GROWTH", "initial_score": 88.0}
            ]
        }
        mock_db.collection("stock_picks").document("current").get.return_value = mock_doc

        with patch("builtins.open", side_effect=Exception("read error")):
            positions = _hydrate_active_positions(mock_db)
            assert any(p["symbol"] == "UNSEEN_SYM" for p in positions)

        # 3. _persist_telemetry exception branches
        mock_db_fail = MagicMock()
        mock_db_fail.collection("bots").document("bot_4").set.side_effect = Exception("set fail")
        mock_doc_fail = MagicMock()
        mock_doc_fail.exists = True
        mock_doc_fail.to_dict.return_value = {"already_accepted": [{"symbol": "NVDA"}]}
        mock_current_ref = MagicMock()
        mock_current_ref.get.return_value = mock_doc_fail
        mock_current_ref.set.side_effect = Exception("set fail")
        mock_db_fail.collection("stock_picks").document.return_value = mock_current_ref

        pos_result = {
            "symbol": "NVDA",
            "client_id": 4,
            "thesis_telemetry": {
                "current_score": 82.0, "score_delta": 2.0, "thesis_state": "HEALTHY",
                "action_instruction": "HOLD", "last_evaluated": "2026-10-11T12:00:00Z"
            }
        }
        _persist_telemetry(mock_db_fail, pos_result)


# ============================================================================
# 5. STOCKPICKER/INCOME_SCREENER.PY GAP FILLING
# ============================================================================

class TestIncomeScreenerGaps:
    def test_fetch_yahoo_options_and_earnings(self):
        from stockpicker.income_screener import (
            fetch_yahoo_options,
            fetch_earnings_date,
            fetch_ticker_quote_metrics,
            evaluate_covered_call_candidate,
            screen_income_candidates,
            _clear_income_screener_caches,
        )

        _clear_income_screener_caches()

        # 1. fetch_yahoo_options 401 retry with crumb refresh and target expiration date
        with patch("requests.get") as mock_get, \
             patch("stockpicker.income_screener.get_yahoo_crumb_and_cookies", return_value=("crumb1", {"B": "cookie"})):
            now_ts = int(time.time())
            exp_target = now_ts + (30 * 86400)
            mock_resp_401 = MagicMock(status_code=401)
            mock_resp_200 = MagicMock(
                status_code=200,
                json=lambda: {
                    "optionChain": {
                        "result": [{
                            "expirationDates": [exp_target],
                            "options": [{"expirationDate": now_ts + 86400}],
                            "quote": {"regularMarketPrice": 150.0}
                        }]
                    }
                }
            )
            mock_resp_exp = MagicMock(
                status_code=200,
                json=lambda: {
                    "optionChain": {
                        "result": [{
                            "expirationDates": [exp_target],
                            "options": [{"expirationDate": exp_target}],
                            "quote": {"regularMarketPrice": 150.0}
                        }]
                    }
                }
            )
            mock_get.side_effect = [mock_resp_401, mock_resp_200, mock_resp_exp]

            res = fetch_yahoo_options("RETRY_SYM")
            assert res is not None

        # 2. fetch_earnings_date with Yahoo 401 retry and Finnhub fallback
        def _fake_earnings_get(url, *args, **kwargs):
            if "finnhub" in url:
                return MagicMock(status_code=200, json=lambda: {"earningsCalendar": [{"date": "2026-11-20"}]})
            return MagicMock(status_code=404)

        with patch("requests.get", side_effect=_fake_earnings_get), \
             patch.dict(os.environ, {"FINNHUB_API_KEY": "test_fh_key"}):
            dt = fetch_earnings_date("FH_SYM")
            assert dt is not None

        # 3. fetch_ticker_quote_metrics with Finnhub fallback
        def _fake_quote_get(url, *args, **kwargs):
            if "finnhub" in url:
                return MagicMock(status_code=200, json=lambda: {"c": 120.0, "h": 125.0, "l": 115.0})
            return MagicMock(status_code=500)

        with patch("requests.get", side_effect=_fake_quote_get), \
             patch.dict(os.environ, {"FINNHUB_API_KEY": "test_fh_key"}):
            metrics = fetch_ticker_quote_metrics("FH_Q_SYM")
            assert metrics is not None
            assert metrics["current_price"] == 120.0

        # 4. evaluate_covered_call_candidate yield and liquidity branches
        call_opt = {
            "strike": 102.0, "bid": 0.80, "ask": 1.00, "lastPrice": 0.80, "openInterest": 30, "impliedVolatility": 0.25
        }
        mock_opt_data = {
            "quote": {"regularMarketPrice": 100.0, "fiftyTwoWeekHigh": 110.0, "fiftyTwoWeekLow": 90.0},
            "options": [{"calls": [call_opt]}]
        }
        with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=mock_opt_data), \
             patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(UTC) + timedelta(days=30)), \
             patch("stockpicker.core.get_finnhub_insider_trades", return_value={"insider_score": -10.0, "net_transactions": -200000}), \
             patch("stockpicker.alternative_data_client.fetch_congressional_trades", return_value=[{"amount": "$50k"}]):
            cand = evaluate_covered_call_candidate("TEST_BRANCH_SYM")
            assert cand is not None
            assert cand["income_score"] > 0

        # 5. screen_income_candidates helper
        with patch("stockpicker.income_screener.evaluate_covered_call_candidate", return_value=cand):
            res_list = screen_income_candidates(["TEST_BRANCH_SYM"], top_n=1)
            assert len(res_list) == 1


# ============================================================================
# 6. STOCKPICKER/FEEDBACK_ENGINE.PY GAP FILLING
# ============================================================================

class TestFeedbackEngineGaps:
    def test_null_db_and_calculation_gaps(self):
        from stockpicker.feedback_engine import (
            archive_pick_snapshot,
            compute_forward_alpha,
            compute_factor_ic,
            load_weight_adjustments,
            ingest_execution_feedback,
            compute_execution_summary,
            analyze_rejection_patterns,
            cleanup_stale_archives,
            _extract_factor_scores,
            _spearman_rank_correlation,
            _safe_nested_get,
            _fetch_current_price,
            _fetch_historical_price,
        )

        # None db checks
        assert archive_pick_snapshot(None, {"ticker": "AAPL"}, "2026-10-10", 100.0) is None
        assert archive_pick_snapshot(MagicMock(), {}, "2026-10-10", 100.0) is None
        assert compute_forward_alpha(None)["status"] == "error"
        assert compute_factor_ic(None)["status"] == "error"
        assert load_weight_adjustments(None) == {}
        assert ingest_execution_feedback(None, "P1", 10.0, 5, "target_hit")["status"] == "error"
        assert compute_execution_summary(None)["status"] == "error"
        assert analyze_rejection_patterns(None)["status"] == "error"
        assert cleanup_stale_archives(None)["status"] == "error"

        # _extract_factor_scores fallback
        scores = _extract_factor_scores({"fundamental_reasons": "invalid string without keys"})
        assert isinstance(scores, dict)

        # _spearman_rank_correlation zero variance
        corr_zero = _spearman_rank_correlation([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])
        assert corr_zero == 0.0

        # _safe_nested_get
        assert _safe_nested_get({"a": {"b": 10}}, "a", "b") == 10
        assert _safe_nested_get(None, "a") is None

        # Price fetch error handlers
        with patch("yfinance.Ticker", side_effect=Exception("yf fail")):
            assert _fetch_current_price("FAIL_SYM") is None
            assert _fetch_historical_price("FAIL_SYM", "2026-10-10") is None


# ============================================================================
# 7. STOCKPICKER/CORE.PY GAP FILLING
# ============================================================================

class TestStockpickerCoreGaps:
    def test_fetch_all_news_fallbacks(self):
        from stockpicker.core import (
            fetch_all_news,
            fetch_newsapi,
            fetch_x_recent,
            fetch_polygon_benzinga,
            fetch_finnhub_news,
            fetch_alphavantage_news,
            compute_fundamental_score,
            heuristic_rank,
        )

        # Unconfigured keys return []
        with patch.dict(os.environ, {
            "NEWSAPI_KEY": "", "X_BEARER_TOKEN": "", "POLYGON_API_KEY": "",
            "FINNHUB_API_KEY": "", "ALPHAVANTAGE_API_KEY": ""
        }):
            assert fetch_newsapi("2026-10-10T00:00:00Z") == []
            assert fetch_x_recent() == []
            assert fetch_polygon_benzinga("2026-10-10") == []
            assert fetch_finnhub_news("2026-10-10T00:00:00Z") == []
            assert fetch_alphavantage_news("2026-10-10T00:00:00Z") == []

            # fetch_all_news fallback cascade
            news = fetch_all_news(hours=12)
            assert isinstance(news, list)

        # compute_fundamental_score
        with patch("stockpicker.core.get_companyfacts_quarterly", return_value={"revenue": [("2025Q3", 100), ("2026Q3", 120)]}), \
             patch("stockpicker.core.get_yahoo_financial_snapshot", return_value={"debt_to_equity": 50, "current_ratio": 1.5, "gross_margins": 0.35, "operating_margins": 0.20, "recommendation_mean": 1.8}), \
             patch("stockpicker.core.get_alpha_earnings", return_value=[]):
            score_data = compute_fundamental_score("AAPL")
            assert score_data is not None

        # heuristic_rank
        items = [{"headline": "FDA approval granted for biotech drug", "summary": "expansion", "tickers": "BMRN"}]
        ranked = heuristic_rank(items)
        assert len(ranked) == 1
        assert ranked[0]["explosiveness"] > 0


# ============================================================================
# 8. MAIN.PY GAP FILLING
# ============================================================================

class TestMainDashboardGaps:
    def test_load_bot_configs_exceptions(self, auth_client):
        client, _ = auth_client

        # FileNotFoundError
        with patch("main.load_bot_configs", side_effect=FileNotFoundError()):
            res1 = client.get("/api/bot-configs")
            assert res1.status_code == 200
            assert "error" in res1.json()

        # JSONDecodeError
        with patch("main.load_bot_configs", side_effect=json.JSONDecodeError("msg", "doc", 0)):
            res2 = client.get("/api/bot-configs")
            assert res2.status_code == 200
            assert "error" in res2.json()

        # Unexpected Exception
        with patch("main.load_bot_configs", side_effect=RuntimeError("disk err")):
            res3 = client.get("/api/bot-configs")
            assert res3.status_code == 200
            assert "error" in res3.json()

    def test_health_check_exceptions(self, auth_client):
        client, _ = auth_client

        # GoogleAPIError in health check
        with patch.object(dashboard_data.db, "collection", side_effect=gcp_exceptions.GoogleAPIError("gcp err")):
            res = client.get("/api/health")
            assert res.status_code == 503
            assert "Firestore" in str(res.json())

        # Unexpected Exception in health check
        with patch.object(dashboard_data.db, "collection", side_effect=RuntimeError("unknown err")):
            res = client.get("/api/health")
            assert res.status_code == 503

        # Config health check failure
        with patch.object(dashboard_data.db, "collection"), \
             patch("main.load_bot_configs", side_effect=Exception("config boom")):
            res = client.get("/api/health")
            assert res.status_code == 503

    def test_bot_control_errors(self, auth_client):
        client, _ = auth_client

        # 1. Stop bots with VM error response and HTTPError
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(is_success=False, status_code=500, json=lambda: {"message": "VM failed"}, text="VM failed")
            res = client.post("/api/bot-control/stop")
            assert res.status_code == 500

            mock_post.side_effect = httpx.ConnectError("network fail")
            res_net = client.post("/api/bot-control/stop")
            assert res_net.status_code == 500

            mock_post.side_effect = RuntimeError("other error")
            res_other = client.post("/api/bot-control/stop")
            assert res_other.status_code == 500

        # 2. Restart bots error paths
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(is_success=False, status_code=500, json=lambda: {"message": "restart failed"}, text="restart failed")
            res = client.post("/api/bot-control/restart")
            assert res.status_code == 500

            mock_post.side_effect = httpx.ConnectError("conn fail")
            res_net = client.post("/api/bot-control/restart")
            assert res_net.status_code == 500

            mock_post.side_effect = RuntimeError("other error")
            res_other = client.post("/api/bot-control/restart")
            assert res_other.status_code == 500

        # 3. Reset bot endpoint
        res_bad = client.post("/api/bot-control/reset/bad-name!")
        assert res_bad.status_code == 400

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"status": "success"}, raise_for_status=lambda: None)
            res_ok = client.post("/api/bot-control/reset/nvda")
            assert res_ok.status_code == 200

            mock_post.side_effect = httpx.ConnectError("conn fail")
            res_err = client.post("/api/bot-control/reset/nvda")
            assert res_err.status_code == 500

    def test_switch_profile_branches(self, auth_client):
        client, _ = auth_client

        # Open positions blocking switch profile
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "bots": [
                {"name": "nvda", "buckets": [{"shares": 100, "symbol": "NVDA", "bucket_type": "upward"}]},
                {"name": "iwm", "buckets": [{"shares": 50, "symbol": "IWM", "bucket_type": "upward"}]},
                {"name": "aapl", "buckets": [{"shares": 25, "symbol": "AAPL", "bucket_type": "upward"}]},
                {"name": "msft", "buckets": [{"shares": 10, "symbol": "MSFT", "bucket_type": "upward"}]},
            ]
        }
        with patch("google.cloud.firestore.Client") as mock_fs, \
             patch("httpx.AsyncClient.get") as mock_get:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db
            mock_db.collection("bot_overview").document("overview").get.return_value = mock_doc
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"profile": "paper"})

            res_block = client.post("/api/bot-control/switch-profile", json={"profile": "live"})
            assert res_block.status_code == 400
            assert "open position" in res_block.json()["detail"].lower()

            # No open positions: proceed and test VM error and audit write exception
            mock_doc_empty = MagicMock()
            mock_doc_empty.exists = True
            mock_doc_empty.to_dict.return_value = {"bots": []}
            mock_db.collection("bot_overview").document("overview").get.return_value = mock_doc_empty
            mock_db.collection("profile_switch_audit").add.side_effect = Exception("audit write err")

            with patch("httpx.AsyncClient.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=400, json=lambda: {"status": "error", "message": "VM rejected"})
                res_vm_err = client.post("/api/bot-control/switch-profile", json={"profile": "live"})
                assert res_vm_err.status_code == 400
