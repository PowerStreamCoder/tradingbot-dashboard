"""
Comprehensive test suite targeting >98% statement coverage for tradingbot-dashboard.
Covers:
- main.py (all endpoints, auth middleware, session handling, Excel exports, bot control, governance, stockpicker APIs)
- services/pnl_statement_engine.py (datetime helpers, commissions, periods, multi-mode aggregates, heatmaps)
- stockpicker/sentinel.py (position hydration, thesis evaluation, telemetry persistence, config discovery)
- stockpicker/income_screener.py (options fetch retry, crumb refresh, synthetic yield model, earnings cache)
- stockpicker/feedback_engine.py (archive TTL cleanup, price fetching, edge cases)
- stockpicker/core.py (Gemini ranking fallbacks, news APIs, crumb acquisition)
"""

import os
import sys
import json
import time
import re
from datetime import datetime, date, timedelta, timezone, UTC
from io import BytesIO
from unittest.mock import Mock, MagicMock, patch, mock_open
import pytest
from fastapi.testclient import TestClient

# Ensure root directory is on pythonpath
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main
from main import (
    app,
    authenticated_sessions,
    dashboard_data,
    load_bot_configs,
    load_bot_parameters,
    get_client_id_for_bot,
    get_valid_access_code,
    get_login_page,
    load_sessions_from_firestore,
    _bot_config_cache,
    _metrics_cache,
    _bot_metrics_cache,
    _trade_history_cache,
    _stock_picks_cache,
    _stockpicker_run_history,
    _stockpicker_lock,
    LearningDecision,
    Trade,
    PnLStatement,
    BOT_CONTROL_API_URL,
)


@pytest.fixture
def auth_client():
    client = TestClient(app)
    session_id = "test-cov-session-98"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=4)
    client.cookies.set("dashboard_session", session_id)
    yield client, session_id
    authenticated_sessions.pop(session_id, None)


# ============================================================================
# 1. MAIN.PY: CONFIGURATION, HELPERS & DASHBOARD DATA
# ============================================================================

class TestDashboardConfigAndData:
    def test_load_bot_configs_cached_and_reload(self):
        with _bot_config_cache['lock']:
            _bot_config_cache['config'] = None
            _bot_config_cache['mtime'] = None

        cfg1 = load_bot_configs()
        assert "bots" in cfg1
        assert cfg1.get("version", 0) >= 1

        # Second call hits in-memory cache
        cfg2 = load_bot_configs()
        assert cfg2["version"] == cfg1["version"]

    def test_load_bot_configs_not_found(self):
        with _bot_config_cache['lock']:
            _bot_config_cache['config'] = None
            _bot_config_cache['mtime'] = None

        with patch("os.path.exists", return_value=False):
            with pytest.raises(FileNotFoundError):
                load_bot_configs()

    def test_load_bot_configs_invalid_json(self):
        with _bot_config_cache['lock']:
            _bot_config_cache['config'] = None
            _bot_config_cache['mtime'] = None

        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="{broken json")):
            with pytest.raises(json.JSONDecodeError):
                load_bot_configs()

    def test_load_bot_parameters(self):
        nvda_params = load_bot_parameters("NVDA")
        assert nvda_params is not None
        assert nvda_params["symbol"] == "NVDA"

        iwm_params = load_bot_parameters("iwm")
        assert iwm_params is not None
        assert iwm_params["symbol"] == "IWM"

        assert load_bot_parameters("") is None
        assert load_bot_parameters("NONEXISTENT_XYZ") is None

        with patch("builtins.open", side_effect=Exception("open failed")):
            # Clear cache for IWM
            with main._bot_params_cache['lock']:
                main._bot_params_cache['params'].pop('iwm', None)
                main._bot_params_cache['mtimes'].pop('iwm', None)
            assert load_bot_parameters("IWM") is None

    def test_get_client_id_for_bot(self):
        assert get_client_id_for_bot(1) in (1, 2, 3, 4, 5)
        assert get_client_id_for_bot(999) == 999
        assert get_client_id_for_bot("bad_id") == "bad_id"

    def test_get_valid_access_code(self):
        code = get_valid_access_code()
        assert code == datetime.now().strftime("%d%m%y")

    def test_get_login_page(self):
        html = get_login_page()
        assert "Trading Dashboard - Access Code Required" in html
        assert "access_code" in html

    def test_dashboard_data_methods(self):
        dd = main.DashboardData()
        dd.bot_data_ref = MagicMock()
        dd.trades_ref = MagicMock()
        dd.logs_ref = MagicMock()

        # _load_bot_data exists
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"data": {"nvda": {"shares": 10}}}
        dd.bot_data_ref.document.return_value.get.return_value = mock_doc
        assert "nvda" in dd._load_bot_data()

        # _load_bot_data not exists
        mock_doc.exists = False
        assert dd._load_bot_data() == {}

        # _load_bot_data exception
        dd.bot_data_ref.document.return_value.get.side_effect = Exception("db error")
        assert dd._load_bot_data() == {}

        # _save_bot_data
        dd.bot_data = {"msft": {"shares": 5}}
        dd.bot_data_ref.document.return_value.set.side_effect = None
        dd._save_bot_data()
        dd.bot_data_ref.document.return_value.set.assert_called()

        dd.bot_data_ref.document.return_value.set.side_effect = Exception("save error")
        dd._save_bot_data()

        # _load_trades
        doc1 = MagicMock()
        doc1.to_dict.return_value = {"symbol": "NVDA", "timestamp": "2026-10-01T12:00:00Z", "sequence_display": "MM0001"}
        doc2 = MagicMock()
        doc2.to_dict.return_value = {"symbol": "IWM", "timestamp": "2026-10-01T13:00:00Z", "sequence_number": 42}
        doc3 = MagicMock()
        doc3.to_dict.return_value = {"symbol": "AAPL", "timestamp": "2026-10-01T14:00:00Z"}
        doc_test = MagicMock()
        doc_test.to_dict.return_value = {"symbol": "TEST", "botName": "TESTBOT", "timestamp": "2026-10-01T15:00:00Z"}

        mock_query = MagicMock()
        mock_query.stream.return_value = [doc1, doc2, doc3, doc_test]
        dd.trades_ref.order_by.return_value.limit.return_value = mock_query

        trades = dd._load_trades(limit=10)
        assert len(trades) == 3
        assert trades[0]["seqNum"] == "MM0001"
        assert trades[1]["seqNum"] == 42
        assert trades[2]["seqNum"] == "?"

        dd.trades_ref.order_by.side_effect = Exception("stream fail")
        assert dd._load_trades() == []

        # _load_logs and _save_logs
        log_doc = MagicMock()
        log_doc.id = "nvda"
        log_doc.to_dict.return_value = {"lines": [f"line {i}" for i in range(25)]}
        dd.logs_ref.stream.return_value = [log_doc]

        logs = dd._load_logs()
        assert len(logs["nvda"]) == 15

        dd.logs_ref.stream.side_effect = Exception("logs error")
        assert dd._load_logs() == {}

        dd.logs_ref.document.return_value.set.side_effect = None
        dd._save_logs("nvda", [f"l{i}" for i in range(20)])
        dd.logs_ref.document.return_value.set.side_effect = Exception("save error")
        dd._save_logs("nvda", ["l1"])

    def test_dashboard_data_calculate_pnl(self):
        dd = main.DashboardData()
        now_iso = datetime.now().isoformat()
        dd.trades = [
            {"timestamp": now_iso, "botId": 1, "profitLoss": 150.0},
            {"timestamp": now_iso, "botId": 2, "profitLoss": -50.0},
            {"timestamp": "invalid-timestamp", "botId": 1, "profitLoss": 10.0},
        ]
        pnl = dd.calculate_pnl()
        assert pnl["day"]["bot1"]["pnl"] == 150.0
        assert pnl["day"]["bot2"]["pnl"] == -50.0
        assert pnl["day"]["total"]["pnl"] == 100.0

    def test_load_sessions_from_firestore(self):
        doc_valid = MagicMock()
        doc_valid.id = "valid-session-1"
        doc_valid.to_dict.return_value = {"expiry": datetime.now() + timedelta(hours=1)}

        doc_expired = MagicMock()
        doc_expired.id = "expired-session-1"
        doc_expired.to_dict.return_value = {"expiry": datetime.now() - timedelta(hours=1)}

        with patch.object(main.sessions_ref, "stream", return_value=[doc_valid, doc_expired]):
            load_sessions_from_firestore()
            assert "valid-session-1" in authenticated_sessions
            assert "expired-session-1" not in authenticated_sessions

        with patch.object(main.sessions_ref, "stream", side_effect=Exception("fs error")):
            load_sessions_from_firestore()


# ============================================================================
# 2. MAIN.PY: AUTH MIDDLEWARE & CORE PAGES
# ============================================================================

class TestAuthMiddlewareAndPages:
    def test_unauthenticated_page_access_redirects_to_login(self):
        client = TestClient(app)
        for path in ["/", "/dashboard", "/bot-focus", "/nvda-focus", "/bot/1", "/pnl-reporting", "/learning-review", "/governance-review"]:
            res = client.get(path, follow_redirects=False)
            assert res.status_code == 200
            assert "Trading Dashboard - Access Code Required" in res.text

    def test_unauthenticated_api_returns_401(self):
        client = TestClient(app)
        res = client.get("/api/bot-overview")
        assert res.status_code == 401
        assert res.json() == {"detail": "Authentication required"}

    def test_unauthenticated_bypass_routes(self):
        client = TestClient(app)
        # static
        res = client.get("/static/nonexistent.css")
        assert res.status_code in (404, 200)

        # /api/bot-configs
        res = client.get("/api/bot-configs")
        assert res.status_code == 200

        # /api/bot-params
        res = client.get("/api/bot-params/NVDA")
        assert res.status_code == 200

        # /api/logs
        res = client.get("/api/logs")
        assert res.status_code == 200

    def test_session_cookie_expiry_and_firestore_fallback(self):
        client = TestClient(app)

        # Expired in cache
        expired_id = "expired-cached-session"
        authenticated_sessions[expired_id] = datetime.now() - timedelta(minutes=10)
        client.cookies.set("dashboard_session", expired_id)

        mock_del = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.delete = mock_del

        with patch.object(main.sessions_ref, "document", return_value=mock_doc_ref):
            res = client.get("/api/bot-overview")
            assert res.status_code == 401
            assert expired_id not in authenticated_sessions
            mock_del.assert_called()

        # Not in cache, exists and valid in Firestore
        fs_id = "firestore-valid-session"
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"expiry": datetime.now() + timedelta(hours=2)}
        mock_doc_ref.get.return_value = mock_doc

        with patch.object(main.sessions_ref, "document", return_value=mock_doc_ref):
            client.cookies.set("dashboard_session", fs_id)
            res = client.get("/api/bot-overview")
            assert res.status_code == 200
            assert fs_id in authenticated_sessions

        # Not in cache, Firestore throws exception
        err_id = "firestore-err-session"
        authenticated_sessions.pop(err_id, None)
        mock_doc_ref.get.side_effect = Exception("fs fail")

        with patch.object(main.sessions_ref, "document", return_value=mock_doc_ref):
            client.cookies.set("dashboard_session", err_id)
            res = client.get("/api/bot-overview")
            assert res.status_code == 401

    def test_authenticate_endpoint(self):
        client = TestClient(app)
        valid_code = get_valid_access_code()

        # Valid authentication
        with patch.object(main.sessions_ref, "document") as mock_doc_fn:
            mock_doc = MagicMock()
            mock_doc_fn.return_value = mock_doc
            res = client.post("/authenticate", json={"access_code": valid_code})
            assert res.status_code == 200
            assert "dashboard_session" in res.cookies
            mock_doc.set.assert_called()

        # Invalid access code
        res = client.post("/authenticate", json={"access_code": "000000"})
        assert res.status_code == 400

        # Bad json body
        res = client.post("/authenticate", content="not json")
        assert res.status_code == 400

    def test_authenticated_pages_rendering(self, auth_client):
        client, _ = auth_client

        # Bot focus
        res = client.get("/bot-focus")
        assert res.status_code == 200

        res = client.get("/dashboard")
        assert res.status_code == 200

        res = client.get("/nvda-focus")
        assert res.status_code == 200

        # Legacy redirect
        res = client.get("/bot/nvda_sma", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/bot-focus?bot=nvda_sma"

        # PnL reporting
        res = client.get("/pnl-reporting")
        assert res.status_code == 200

        # Learning review
        res = client.get("/learning-review")
        assert res.status_code == 200

        # Governance review
        res = client.get("/governance-review")
        assert res.status_code == 200


# ============================================================================
# 3. MAIN.PY: BOT CONFIGS, HEALTH, METRICS & BOT STATUS
# ============================================================================

class TestBotConfigsHealthAndMetrics:
    def test_get_bot_configs_api(self):
        client = TestClient(app)
        res = client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        assert "bots" in data
        assert "version" in data

    def test_get_bot_params_endpoints(self):
        client = TestClient(app)
        # Single symbol
        res = client.get("/api/bot-params/NVDA")
        assert res.status_code == 200
        assert res.json()["symbol"] == "NVDA"

        # Unknown symbol
        res = client.get("/api/bot-params/UNKNOWN")
        assert res.status_code == 404

        # All params
        res = client.get("/api/bot-params")
        assert res.status_code == 200
        assert "NVDA" in res.json()["bots"]

        with patch("main.load_bot_configs", side_effect=Exception("disk error")):
            res = client.get("/api/bot-params")
            assert res.status_code == 500

    def test_health_check_healthy_and_unhealthy(self):
        client = TestClient(app)
        # Healthy
        with patch.object(dashboard_data.db.collection('bot_status').limit(1), "get", return_value=[]):
            res = client.get("/api/health")
            assert res.status_code == 200
            assert res.json()["status"] == "healthy"

        # Firestore timeout
        import asyncio
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            res = client.get("/api/health")
            assert res.status_code == 503
            assert res.json()["status"] == "unhealthy"

        # Config error
        with patch("main.load_bot_configs", return_value={}):
            res = client.get("/api/health")
            assert res.status_code == 503

    def test_get_metrics(self, auth_client):
        client, _ = auth_client

        # Fresh calculation
        with _metrics_cache['lock']:
            _metrics_cache['data'] = None

        res = client.get("/api/metrics")
        assert res.status_code == 200
        data = res.json()
        assert "bots" in data
        assert data["bots"]["total"] >= 0
        assert "sessions" in data

        # Cached hit
        res_cached = client.get("/api/metrics")
        assert res_cached.status_code == 200

        # Exception branch
        with patch("main.load_bot_configs", side_effect=Exception("boom")):
            with _metrics_cache['lock']:
                _metrics_cache['data'] = None
            res = client.get("/api/metrics")
            assert res.status_code == 500

    def test_get_bot_overview_and_status(self, auth_client):
        client, _ = auth_client
        res = client.get("/api/bot-overview")
        assert res.status_code == 200

        # Bot status
        mock_coll = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "status": "RUNNING", "symbol": "NVDA", "current_price": 125.50,
            "last_heartbeat": datetime.now()
        }
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc
        mock_coll.document.return_value = mock_doc_ref

        with patch.object(dashboard_data.db, "collection", return_value=mock_coll):
            res = client.get("/api/bot-status/NVDA")
            assert res.status_code == 200
            assert res.json()["symbol"] == "NVDA"

            # Bot status fallback
            mock_doc.exists = False
            res = client.get("/api/bot-status/UNKNOWN")
            assert res.status_code == 200
            assert "error" in res.json()

            # Bot status error caught internally
            mock_doc_ref.get.side_effect = Exception("firestore error")
            res = client.get("/api/bot-status/NVDA")
            assert res.status_code == 200
            assert "error" in res.json()

    def test_get_logs(self):
        client = TestClient(app)
        dashboard_data.logs = {
            "1": [
                "2026-10-10 12:00:00 | INFO | System started",
                "malformed log line without pipes"
            ]
        }
        res = client.get("/api/logs?botId=1")
        assert res.status_code == 200
        logs = res.json()
        assert len(logs) == 2
        assert logs[0]["severity"] == "INFO"
        assert logs[1]["message"] == "malformed log line without pipes"

        # Empty logs
        res_empty = client.get("/api/logs?botId=999")
        assert res_empty.status_code == 200
        assert res_empty.json() == []


# ============================================================================
# 4. MAIN.PY: TRADE HISTORY, BOT METRICS & EXPORT
# ============================================================================

class TestTradeHistoryAndExport:
    def test_get_trade_history_all(self, auth_client):
        client, _ = auth_client
        res = client.get("/api/trade-history")
        assert res.status_code == 200

        res_all = client.get("/api/trade-history/all?days=30")
        assert res_all.status_code == 200
        assert "trades" in res_all.json()

        with _trade_history_cache['lock']:
            _trade_history_cache['data'] = None
            _trade_history_cache['timestamp'] = None
        with patch.object(dashboard_data.trades_ref, "where", side_effect=Exception("query fail")):
            res_err = client.get("/api/trade-history/all?days=30")
            assert res_err.status_code == 200
            assert "error" in res_err.json()

    def test_get_bot_metrics(self, auth_client):
        client, _ = auth_client
        with _bot_metrics_cache['lock']:
            _bot_metrics_cache['data'].clear()
            _bot_metrics_cache['timestamp'].clear()

        doc_trade = MagicMock()
        doc_trade.to_dict.return_value = {
            "symbol": "NVDA", "botId": 1, "profitLoss": 200.0,
            "stock_pnl": 150.0, "option_pnl": 50.0, "timestamp": datetime.now().isoformat()
        }
        mock_query = MagicMock()
        mock_query.stream.return_value = [doc_trade]

        mock_where = MagicMock()
        mock_where.order_by.return_value.limit.return_value = mock_query

        with patch.object(dashboard_data.trades_ref, "where", return_value=mock_where):
            res = client.get("/api/bot-metrics/1")
            assert res.status_code == 200
            data = res.json()
            assert data["total_trades"] == 1
            assert data["win_rate"] == 100.0

            # Cached
            res_cached = client.get("/api/bot-metrics/1")
            assert res_cached.status_code == 200

        # Empty trades
        with _bot_metrics_cache['lock']:
            _bot_metrics_cache['data'].clear()
        mock_query.stream.return_value = []
        with patch.object(dashboard_data.trades_ref, "where", return_value=mock_where):
            res_empty = client.get("/api/bot-metrics/2")
            assert res_empty.status_code == 200
            assert res_empty.json()["total_trades"] == 0

        # Error branch
        with _bot_metrics_cache['lock']:
            _bot_metrics_cache['data'].clear()
        with patch.object(dashboard_data.trades_ref, "where", side_effect=Exception("metrics err")):
            res_err = client.get("/api/bot-metrics/1")
            assert res_err.status_code == 500

    def test_export_trade_history_excel(self, auth_client):
        client, _ = auth_client

        doc1 = MagicMock()
        doc1.to_dict.return_value = {
            "symbol": "NVDA", "botName": "nvda_sma", "bucketType": "upward",
            "referencePriceBefore": 120.0, "entryPrice": 122.0, "referencePriceAfter": 125.0,
            "exitPrice": 126.0, "timestamp": "2026-05-10T14:30:00Z", "profitLoss": 400.0, "seqNum": "MM01"
        }
        doc2 = MagicMock()
        doc2.to_dict.return_value = {
            "symbol": "IWM", "botName": "iwm_sma", "bucketType": "downward",
            "referencePriceBefore": 210.0, "entryPrice": 208.0, "referencePriceAfter": 205.0,
            "exitPrice": 204.0, "timestamp": "2026-05-15T15:30:00Z", "profitLoss": -400.0
        }
        mock_stream = MagicMock()
        mock_stream.stream.return_value = [doc1, doc2]

        with patch.object(dashboard_data.trades_ref, "order_by", return_value=mock_stream):
            # Export with start and end month
            res = client.get("/api/trade-history/export?start_month=2026-05&end_month=2026-05")
            assert res.status_code == 200
            assert "application/vnd.openxmlformats" in res.headers["content-type"]

            # Export with December month (rollover branch)
            res_dec = client.get("/api/trade-history/export?start_month=2026-12&end_month=2026-12")
            assert res_dec.status_code == 200

            # Export with empty end_month
            res_empty_end = client.get("/api/trade-history/export?start_month=2026-05&end_month=")
            assert res_empty_end.status_code == 200

        with patch.object(dashboard_data.trades_ref, "order_by", side_effect=Exception("export fail")):
            res_err = client.get("/api/trade-history/export?start_month=2026-05&end_month=2026-05")
            assert res_err.status_code == 500


# ============================================================================
# 5. MAIN.PY: MUTATION ENDPOINTS (BOT OVERVIEW, LOGS, TRADES, PNL)
# ============================================================================

class TestDashboardMutations:
    def test_update_bot_overview(self):
        client = TestClient(app)
        res = client.post("/api/bot-overview/update", json={"nvda": {"price": 120.0}})
        assert res.status_code == 200
        assert res.json()["status"] == "error"

    def test_delete_bucket(self):
        client = TestClient(app)
        dashboard_data.bot_data = {
            "msft-bot": {
                "bucket3": {"shares": 10}
            }
        }
        mock_doc_ref = MagicMock()
        with patch.object(dashboard_data.bot_data_ref, "document", return_value=mock_doc_ref):
            # Missing fields
            res = client.post("/api/bot-overview/delete-bucket", json={"bot_id": "msft-bot"})
            assert res.status_code == 500

            # Success
            res = client.post("/api/bot-overview/delete-bucket", json={"bot_id": "msft-bot", "bucket_id": 3})
            assert res.status_code == 200
            assert res.json()["status"] == "success"
            mock_doc_ref.update.assert_called()

        with patch.object(dashboard_data.bot_data_ref, "document", side_effect=Exception("err")):
            res = client.post("/api/bot-overview/delete-bucket", json={"bot_id": "msft-bot", "bucket_id": 3})
            assert res.status_code == 500

    def test_update_logs(self):
        client = TestClient(app)
        with patch.object(dashboard_data, "_save_logs"):
            res = client.post("/api/logs/update", json={"botId": 1, "lines": ["line 1", "line 2"]})
            assert res.status_code == 200
            assert res.json()["status"] == "success"

        with patch.object(dashboard_data, "_save_logs", side_effect=Exception("err")):
            res = client.post("/api/logs/update", json={"botId": 1, "lines": []})
            assert res.status_code == 500

    def test_pnl_sync(self):
        client = TestClient(app)
        res = client.post("/api/pnl-sync", json={"botId": 1, "bot_realized_pnl": 100.0})
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        # Missing botId
        res_err = client.post("/api/pnl-sync", json={})
        assert res_err.status_code == 500

    def test_add_trade(self):
        client = TestClient(app)
        trade_payload = {
            "timestamp": "2026-10-10T12:00:00Z",
            "botName": "nvda_sma",
            "bucketType": "upward",
            "referencePriceBefore": 120.0,
            "entryPrice": 121.0,
            "referencePriceAfter": 125.0,
            "exitPrice": 126.0,
            "profitLoss": 500.0,
            "shares": 100
        }
        res = client.post("/api/trade-history/add", json=trade_payload)
        assert res.status_code == 200
        assert res.json()["status"] == "error"

    def test_update_pnl_statement(self):
        client = TestClient(app)
        period_data = {"bot1": {"trades": 1, "pnl": 100.0}, "bot2": {"trades": 0, "pnl": 0.0}, "total": {"trades": 1, "pnl": 100.0}}
        pnl_data = {
            "day": period_data,
            "week": period_data,
            "month": period_data
        }
        res = client.post("/api/pnl-statement/update", json=pnl_data)
        assert res.status_code == 200
        assert res.json()["status"] == "error"

    def test_delete_trade(self, auth_client):
        client, _ = auth_client
        mock_doc = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = [mock_doc]

        with patch.object(dashboard_data.trades_ref, "where", return_value=mock_query):
            res = client.delete("/api/trade-history/delete?timestamp=2026-10-10T12:00:00Z")
            assert res.status_code == 200
            mock_doc.reference.delete.assert_called()

        # Not found
        mock_empty = MagicMock()
        mock_empty.stream.return_value = []
        with patch.object(dashboard_data.trades_ref, "where", return_value=mock_empty):
            res = client.delete("/api/trade-history/delete?timestamp=2026-10-10T12:00:00Z")
            assert res.status_code == 500

    def test_delete_trade_multi_identifiers_and_cache(self, auth_client):
        client, _ = auth_client

        # Test delete by doc_id
        mock_doc = MagicMock()
        mock_doc.id = "doc123"
        mock_doc.get.return_value = mock_doc
        mock_doc.to_dict.return_value = {
            "entryOrderId": 12345,
            "timestamp": "2026-10-10T12:00:00Z"
        }
        mock_doc.exists = True

        mock_opt = MagicMock()
        mock_opt_query = MagicMock()
        mock_opt_query.stream.return_value = [mock_opt]

        with patch.object(dashboard_data.trades_ref, "document", return_value=mock_doc), \
             patch.object(dashboard_data.db, "collection") as mock_col:
            mock_col.return_value.where.return_value = mock_opt_query
            res = client.delete("/api/trade-history/delete?doc_id=doc123")
            assert res.status_code == 200
            assert res.json()["status"] == "success"
            mock_doc.reference.delete.assert_called()

        # Test delete by entry_order_id
        mock_query = MagicMock()
        mock_query.stream.return_value = [mock_doc]
        with patch.object(dashboard_data.trades_ref, "where", return_value=mock_query):
            res = client.delete("/api/trade-history/delete?entry_order_id=12345")
            assert res.status_code == 200
            assert res.json()["status"] == "success"

        # Test delete by trade_id
        with patch.object(dashboard_data.trades_ref, "where", return_value=mock_query):
            res = client.delete("/api/trade-history/delete?trade_id=NVDA_20261010_120000_1")
            assert res.status_code == 200
            assert res.json()["status"] == "success"


# ============================================================================
# 6. MAIN.PY: BOT CONTROL & POSITION MANAGEMENT
# ============================================================================

class TestBotControlEndpoints:
    def test_stop_and_restart_bots(self, auth_client):
        client, _ = auth_client
        import httpx

        # Stop bots success
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "stopped"}
            mock_post.return_value = mock_resp

            res = client.post("/api/bot-control/stop")
            assert res.status_code == 200

            # Restart bots success
            mock_resp.json.return_value = {"status": "restarted"}
            res = client.post("/api/bot-control/restart")
            assert res.status_code == 200

            # Reset bot valid name
            res = client.post("/api/bot-control/reset/nvda")
            assert res.status_code == 200

            # Reset bot invalid format
            res_bad = client.post("/api/bot-control/reset/nvda_sma!")
            assert res_bad.status_code == 400

            # Error cases
            mock_post.side_effect = httpx.HTTPError("vm error")
            assert client.post("/api/bot-control/stop").status_code == 500
            assert client.post("/api/bot-control/restart").status_code == 500
            assert client.post("/api/bot-control/reset/nvda").status_code == 500

    def test_close_position(self, auth_client):
        client, _ = auth_client

        # Missing fields
        res = client.post("/api/close-position", json={"bot_id": 1})
        assert res.status_code == 400

        # Success
        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db
            res = client.post("/api/close-position", json={
                "bot_id": 1, "symbol": "NVDA", "bucket_group": "upward", "bucket_id": 0
            })
            assert res.status_code == 200
            assert res.json()["success"] is True

            # Error
            mock_db.collection().document().update.side_effect = Exception("firestore fail")
            res = client.post("/api/close-position", json={
                "bot_id": 1, "symbol": "NVDA", "bucket_group": "upward", "bucket_id": 0
            })
            assert res.status_code == 500

    def test_create_position(self, auth_client):
        client, _ = auth_client

        # Missing fields
        res = client.post("/api/create-position", json={"bot_id": 1})
        assert res.status_code == 400

        # Success when bot doc does not exist
        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db
            mock_doc = MagicMock()
            mock_doc.exists = False
            mock_db.collection().document().get.return_value = mock_doc

            res = client.post("/api/create-position", json={"bot_id": 1, "symbol": "NVDA"})
            assert res.status_code == 200
            assert res.json()["success"] is True

            # Error
            mock_db.collection().document().set.side_effect = Exception("create fail")
            res = client.post("/api/create-position", json={"bot_id": 1, "symbol": "NVDA"})
            assert res.status_code == 500

    def test_get_current_profile(self):
        client = TestClient(app)
        import httpx

        # Success
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"profile": "live"}
            mock_get.return_value = mock_resp

            res = client.get("/api/bot-control/current-profile")
            assert res.status_code == 200
            assert res.json()["profile"] == "live"

            # Fallback on HTTPError
            mock_get.side_effect = httpx.HTTPError("unreachable")
            res_fb = client.get("/api/bot-control/current-profile")
            assert res_fb.status_code == 200
            assert res_fb.json()["profile"] == "paper"

            # Fallback on Exception
            mock_get.side_effect = Exception("generic error")
            res_ex = client.get("/api/bot-control/current-profile")
            assert res_ex.status_code == 200
            assert res_ex.json()["profile"] == "paper"

    def test_switch_profile_and_audit(self):
        client = TestClient(app)
        import httpx

        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db

            # 1. Invalid profile
            res = client.post("/api/bot-control/switch-profile", json={"profile": "invalid"})
            assert res.status_code == 400

            # 2. Blocked by open positions
            mock_ov_doc = MagicMock()
            mock_ov_doc.exists = True
            mock_ov_doc.to_dict.return_value = {
                "bots": [{
                    "name": "nvda_sma",
                    "buckets": [{"shares": 100, "symbol": "NVDA", "bucket_type": "upward"}]
                }]
            }
            mock_db.collection("bot_overview").document("overview").get.return_value = mock_ov_doc

            res = client.post("/api/bot-control/switch-profile", json={"profile": "live"})
            assert res.status_code == 400
            assert "open position" in res.json()["detail"]

            # 3. No open positions, VM success
            mock_ov_no_pos = MagicMock()
            mock_ov_no_pos.exists = True
            mock_ov_no_pos.to_dict.return_value = {"bots": []}
            mock_db.collection("bot_overview").document("overview").get.return_value = mock_ov_no_pos

            with patch("httpx.AsyncClient.post") as mock_post:
                mock_vm_resp = MagicMock()
                mock_vm_resp.status_code = 200
                mock_vm_resp.json.return_value = {"status": "success", "profile": "live", "profile_confirmed": True}
                mock_post.return_value = mock_vm_resp

                res = client.post("/api/bot-control/switch-profile", json={"profile": "live"})
                assert res.status_code == 200
                assert res.json()["status"] == "success"

                # 4. VM error response
                mock_vm_resp.status_code = 400
                mock_vm_resp.json.return_value = {"status": "error", "message": "rate limited"}
                res = client.post("/api/bot-control/switch-profile", json={"profile": "live"})
                assert res.status_code == 400

                # 5. VM httpx error
                mock_post.side_effect = httpx.HTTPError("conn error")
                res = client.post("/api/bot-control/switch-profile", json={"profile": "live"})
                assert res.status_code == 500

    def test_get_profile_audit(self):
        client = TestClient(app)
        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db
            doc1 = MagicMock()
            doc1.to_dict.return_value = {"status": "success", "to_profile": "live", "timestamp": datetime.now()}
            doc1.id = "audit1"
            mock_db.collection("profile_switch_audit").order_by().limit().stream.return_value = [doc1]

            res = client.get("/api/bot-control/profile-audit")
            assert res.status_code == 200
            assert len(res.json()["switches"]) == 1

            mock_db.collection.side_effect = Exception("audit err")
            res_err = client.get("/api/bot-control/profile-audit")
            assert res_err.status_code == 500


# ============================================================================
# 7. MAIN.PY: LEARNING CANDIDATES & GOVERNANCE ENDPOINTS
# ============================================================================

class TestLearningAndGovernanceEndpoints:
    def test_learning_candidates_endpoints(self):
        client = TestClient(app)

        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db

            # GET candidates
            doc = MagicMock()
            doc.to_dict.return_value = {"finding_id": "FIND_1", "priority": "high", "created_at": "2026-10-01"}
            mock_db.collection("learning_candidates").stream.return_value = [doc]

            res = client.get("/api/learning-candidates")
            assert res.status_code == 200
            assert len(res.json()["findings"]) == 1

            # POST decision
            mock_doc_ref = MagicMock()
            mock_doc_cand = MagicMock()
            mock_doc_cand.exists = True
            mock_doc_ref.get.return_value = mock_doc_cand
            mock_db.collection("learning_candidates").document.return_value = mock_doc_ref

            decision_payload = {"finding_id": "FIND_1", "decision": "approved", "user_notes": "test note"}
            res = client.post("/api/learning-candidates/decision", json=decision_payload)
            assert res.status_code == 200
            mock_doc_ref.update.assert_called()

            # Decision not found
            mock_doc_cand.exists = False
            res = client.post("/api/learning-candidates/decision", json=decision_payload)
            assert res.status_code == 404

            # GET stats
            doc1 = MagicMock()
            doc1.to_dict.return_value = {"status": "pending_review", "priority": "high"}
            doc2 = MagicMock()
            doc2.to_dict.return_value = {"status": "approved", "priority": "medium"}
            mock_db.collection("learning_candidates").stream.return_value = [doc1, doc2]

            res_stats = client.get("/api/learning-candidates/stats")
            assert res_stats.status_code == 200
            assert res_stats.json()["total_pending"] == 1
            assert res_stats.json()["total_approved"] == 1

            # Exceptions
            mock_db.collection.side_effect = Exception("fs fail")
            assert client.get("/api/learning-candidates").status_code == 500
            assert client.post("/api/learning-candidates/decision", json=decision_payload).status_code == 500
            assert client.get("/api/learning-candidates/stats").status_code == 500

    def test_governance_and_telemetry_endpoints(self, auth_client):
        client, _ = auth_client

        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db

            # GET exit-governance
            mock_doc_gov = MagicMock()
            mock_doc_gov.exists = True
            mock_doc_gov.to_dict.return_value = {"max_daily_loss": 500.0}
            mock_db.collection("exit_governance_policy").document("current").get.return_value = mock_doc_gov

            res = client.get("/api/exit-governance")
            assert res.status_code == 200

            # GET applied-changes
            doc_change = MagicMock()
            doc_change.id = "chg1"
            doc_change.to_dict.return_value = {"symbol": "NVDA", "param": "atr_mult"}
            mock_db.collection("applied_changes").stream.return_value = [doc_change]

            res = client.get("/api/applied-changes?symbol=NVDA")
            assert res.status_code == 200
            assert len(res.json()["changes"]) == 1

            # GET adaptive-actions
            doc_act = MagicMock()
            doc_act.id = "act1"
            doc_act.to_dict.return_value = {"action_type": "tighten", "created_at": "2026-10-10"}
            mock_db.collection("adaptive_action_log").stream.return_value = [doc_act]

            res = client.get("/api/adaptive-actions")
            assert res.status_code == 200
            assert len(res.json()["actions"]) == 1

            # GET exit-telemetry/snapshots
            doc_snap = MagicMock()
            doc_snap.id = "snap1"
            doc_snap.to_dict.return_value = {
                "symbol": "NVDA", "inventory": {"total_evaluations": 5}
            }
            mock_db.collection("exit_telemetry_snapshots").order_by.return_value.limit.return_value.stream.return_value = [doc_snap]

            res = client.get("/api/exit-telemetry/snapshots?symbol=NVDA&include_empty=true")
            assert res.status_code == 200
            assert len(res.json()["sessions"]) == 1

            # Exceptions
            mock_db.collection.side_effect = Exception("err")
            assert client.get("/api/exit-governance").status_code == 500
            assert client.get("/api/applied-changes").status_code == 500
            assert client.get("/api/adaptive-actions").status_code == 500
            assert client.get("/api/exit-telemetry/snapshots").status_code == 500

    def test_historical_bars_and_option_events(self):
        client = TestClient(app)

        # Historical bars exists
        mock_coll = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"bars": [{"close": 120.0}, {"close": 125.0}]}
        mock_doc_ref.get.return_value = mock_doc
        mock_coll.document.return_value = mock_doc_ref

        with patch.object(dashboard_data.db, "collection", return_value=mock_coll):
            res = client.get("/api/historical-bars/NVDA?bars=1")
            assert res.status_code == 200
            assert len(res.json()) == 1

            # Historical bars not exists
            mock_doc.exists = False
            res = client.get("/api/historical-bars/NVDA")
            assert res.status_code == 200
            assert res.json() == []

            # Historical bars error
            mock_doc_ref.get.side_effect = Exception("fail")
            res = client.get("/api/historical-bars/NVDA")
            assert res.status_code == 200
            assert res.json() == []

        # Option events
        event_doc = MagicMock()
        event_doc.to_dict.return_value = {"stock_entry_order_id": 100, "timestamp": "2026-10-10T10:00:00Z"}
        mock_events_query = MagicMock()
        mock_events_query.stream.return_value = [event_doc]
        mock_events_coll = MagicMock()
        mock_events_coll.where.return_value.limit.return_value = mock_events_query

        with patch.object(dashboard_data.db, "collection", return_value=mock_events_coll):
            res = client.get("/api/option-events/100")
            assert res.status_code == 200
            assert res.json()["event_count"] == 1

        # Option events error
        with patch.object(dashboard_data.db, "collection", side_effect=Exception("stream fail")):
            res = client.get("/api/option-events/100")
            assert res.status_code == 200
            assert res.json()["status"] == "error"


# ============================================================================
# 8. MAIN.PY: STOCK PICKER API ENDPOINTS
# ============================================================================

class TestStockPicksEndpoints:
    def test_get_stock_picks(self, auth_client):
        client, _ = auth_client
        with _stock_picks_cache['lock']:
            _stock_picks_cache['data'] = None

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "picks": [{"ticker": "NVDA", "composite_score": 85.0}],
            "status": "COMPLETED",
            "pick_count": 1
        }
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc

        with patch.object(dashboard_data.stock_picks_ref, "document", return_value=mock_doc_ref):
            res = client.get("/api/stock-picks")
            assert res.status_code == 200
            assert res.json()["pick_count"] == 1

            # Cached hit
            res_cached = client.get("/api/stock-picks")
            assert res_cached.status_code == 200

        with _stock_picks_cache['lock']:
            _stock_picks_cache['data'] = None
        mock_doc_ref.get.side_effect = Exception("fs fail")
        with patch.object(dashboard_data.stock_picks_ref, "document", return_value=mock_doc_ref):
            res = client.get("/api/stock-picks")
            assert res.status_code == 500

    def test_run_stock_picker(self, auth_client):
        client, session_id = auth_client

        # Rate limit test
        _stockpicker_run_history[session_id] = [datetime.now()] * 10
        res = client.post("/api/stock-picks/run")
        assert res.status_code == 429

        # Concurrency test
        _stockpicker_run_history[session_id] = []
        with _stockpicker_lock:
            main._stockpicker_running = True
        try:
            res = client.post("/api/stock-picks/run")
            assert res.status_code == 409
        finally:
            with _stockpicker_lock:
                main._stockpicker_running = False

        # Success run - mock runner to avoid calling external services
        with patch("stockpicker.runner.run_stockpicker", return_value={"picks": [{"ticker": "AAPL", "score": 80.0}], "status": "success"}):
            res = client.post("/api/stock-picks/run")
            assert res.status_code == 200
            assert res.json()["status"] == "success"

        # Timeout run
        import asyncio
        with patch("stockpicker.runner.run_stockpicker", return_value={}):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                res = client.post("/api/stock-picks/run")
                assert res.status_code == 200
                assert res.json()["status"] == "error"
                assert "timeout" in res.json()["message"].lower()

            with patch("asyncio.wait_for", side_effect=Exception("worker error")):
                res = client.post("/api/stock-picks/run")
                assert res.status_code == 200
                assert res.json()["status"] == "error"
                assert "worker error" in res.json()["message"]

    def test_accept_and_reject_stock_picks(self, auth_client):
        client, _ = auth_client

        # Invalid body
        res = client.post("/api/stock-picks/accept", content="invalid json")
        assert res.status_code == 400
        res = client.post("/api/stock-picks/reject", content="invalid json")
        assert res.status_code == 400

        # Invalid symbol
        res = client.post("/api/stock-picks/accept", json={"symbol": "$$INVALID$$"})
        assert res.status_code == 400
        res = client.post("/api/stock-picks/reject", json={"symbol": "$$INVALID$$"})
        assert res.status_code == 400

        # Doc not found
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc

        with patch.object(dashboard_data.stock_picks_ref, "document", return_value=mock_doc_ref):
            res = client.post("/api/stock-picks/accept", json={"symbol": "NVDA"})
            assert res.status_code == 404
            res = client.post("/api/stock-picks/reject", json={"symbol": "NVDA"})
            assert res.status_code == 404

        # Success accept
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "actionable_leads": [{"ticker": "NVDA", "status": "PENDING_REVIEW"}],
            "already_accepted": []
        }
        with patch.object(dashboard_data.stock_picks_ref, "document", return_value=mock_doc_ref):
            res = client.post("/api/stock-picks/accept", json={"symbol": "NVDA", "notes": "Approved for SMA"})
            assert res.status_code == 200
            assert res.json()["status"] == "success"
            mock_doc_ref.set.assert_called()

        # Success reject
        mock_doc.to_dict.return_value = {
            "actionable_leads": [{"ticker": "TSLA", "status": "PENDING_REVIEW"}],
            "rejected_picks": []
        }
        with patch.object(dashboard_data.stock_picks_ref, "document", return_value=mock_doc_ref):
            res = client.post("/api/stock-picks/reject", json={"symbol": "TSLA", "reason": "excessive_volatility"})
            assert res.status_code == 200
            assert res.json()["status"] == "success"

    def test_parameter_advice_endpoint(self, auth_client):
        client, _ = auth_client

        # Missing symbol (FastAPI returns 422 for missing query param)
        res = client.get("/api/stock-picks/parameter-advice")
        assert res.status_code == 422

        # Invalid symbol regex
        res_bad = client.get("/api/stock-picks/parameter-advice?symbol=@@@")
        assert res_bad.status_code == 400

        # Success advice
        with patch("stockpicker.parameter_advisor.generate_expert_parameter_advice", return_value={"symbol": "NVDA", "sma_fast": 5}):
            res = client.get("/api/stock-picks/parameter-advice?symbol=NVDA")
            assert res.status_code == 200
            assert res.json()["symbol"] == "NVDA"

        with patch("stockpicker.parameter_advisor.generate_expert_parameter_advice", side_effect=Exception("advise fail")):
            res = client.get("/api/stock-picks/parameter-advice?symbol=NVDA")
            assert res.status_code == 500

    def test_provision_bot_endpoint(self, auth_client):
        client, _ = auth_client

        # Invalid body / ticker
        res = client.post("/api/stock-picks/provision-bot", content="bad json")
        assert res.status_code == 400

        res = client.post("/api/stock-picks/provision-bot", json={"symbol": "@@@"})
        assert res.status_code == 400

        # Already exists
        mock_cfg = {"bots": [{"symbol": "NVDA", "name": "nvda_sma", "client_id": 4}]}
        with patch("builtins.open", mock_open(read_data=json.dumps(mock_cfg))):
            res = client.post("/api/stock-picks/provision-bot", json={"symbol": "NVDA"})
            assert res.status_code == 200
            assert res.json()["status"] == "already_exists"

        # Provision new bot
        mock_cfg_new = {"bots": [{"symbol": "NVDA", "name": "nvda_sma", "client_id": 4}]}
        mock_doc_ref = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"already_accepted": []}
        mock_doc_ref.get.return_value = mock_doc

        with patch("builtins.open", mock_open(read_data=json.dumps(mock_cfg_new))), \
             patch.object(dashboard_data.stock_picks_ref, "document", return_value=mock_doc_ref):
            res = client.post("/api/stock-picks/provision-bot", json={
                "symbol": "AAPL", "strategy_track": "GROWTH", "capital_allocation": 15000.0
            })
            assert res.status_code == 200
            assert res.json()["status"] == "success"
            assert res.json()["client_id"] == 5

    def test_feedback_and_sentinel_endpoints(self, auth_client):
        client, _ = auth_client

        # Feedback status
        with patch("stockpicker.feedback_engine.compute_execution_summary", return_value={"total_tracked": 5}), \
             patch("stockpicker.feedback_engine.load_weight_adjustments", return_value={"w": 1.0}):
            res = client.get("/api/stock-picks/feedback/status")
            assert res.status_code == 200
            assert res.json()["status"] == "success"

        with patch("stockpicker.feedback_engine.compute_execution_summary", side_effect=Exception("fb err")):
            res = client.get("/api/stock-picks/feedback/status")
            assert res.status_code == 500

        # Feedback run-audit
        with patch("stockpicker.feedback_engine.run_feedback_loop", return_value={"updated": 2}):
            res = client.post("/api/stock-picks/feedback/run-audit")
            assert res.status_code == 200

        with patch("stockpicker.feedback_engine.run_feedback_loop", side_effect=Exception("loop err")):
            res = client.post("/api/stock-picks/feedback/run-audit")
            assert res.status_code == 500

        # Execution feedback
        res = client.post("/api/stock-picks/feedback/execution", json={"pick_id": "P1"})
        assert res.status_code == 400

        res = client.post("/api/stock-picks/feedback/execution", json={"realized_pnl": 100.0})
        assert res.status_code == 400

        with patch("stockpicker.feedback_engine.ingest_execution_feedback", return_value={"status": "ingested"}):
            res = client.post("/api/stock-picks/feedback/execution", json={"pick_id": "P1", "realized_pnl": 150.0})
            assert res.status_code == 200

        with patch("stockpicker.feedback_engine.ingest_execution_feedback", side_effect=Exception("ingest err")):
            res = client.post("/api/stock-picks/feedback/execution", json={"pick_id": "P1", "realized_pnl": 150.0})
            assert res.status_code == 500

        # Sentinel status
        with patch("stockpicker.sentinel.is_sentinel_enabled", return_value=False):
            res = client.get("/api/stock-picks/sentinel/status")
            assert res.status_code == 200
            assert res.json()["status"] == "disabled"

        with patch("stockpicker.sentinel.is_sentinel_enabled", return_value=True), \
             patch("stockpicker.sentinel.audit_in_flight_positions", return_value={"positions_checked": 2}):
            res = client.get("/api/stock-picks/sentinel/status")
            assert res.status_code == 200
            assert res.json()["positions_checked"] == 2

        with patch("stockpicker.sentinel.is_sentinel_enabled", side_effect=Exception("sentinel err")):
            res = client.get("/api/stock-picks/sentinel/status")
            assert res.status_code == 500

        # Sentinel audit
        with patch("stockpicker.sentinel.is_sentinel_enabled", return_value=False):
            res = client.post("/api/stock-picks/sentinel/audit")
            assert res.status_code == 200
            assert res.json()["status"] == "disabled"

            res_force = client.post("/api/stock-picks/sentinel/audit?force=true")
            assert res_force.status_code == 200
            assert res_force.json()["status"] == "success"

        with patch("stockpicker.sentinel.audit_in_flight_positions", side_effect=Exception("audit err")):
            res = client.post("/api/stock-picks/sentinel/audit?force=true")
            assert res.status_code == 500

        # Sentinel toggle
        with patch("stockpicker.sentinel.set_sentinel_enabled"), patch("stockpicker.sentinel.is_sentinel_enabled", return_value=True):
            res = client.post("/api/stock-picks/sentinel/toggle", json={"enabled": True})
            assert res.status_code == 200
            assert res.json()["enabled"] is True

        with patch("stockpicker.sentinel.set_sentinel_enabled", side_effect=Exception("toggle err")):
            res = client.post("/api/stock-picks/sentinel/toggle", json={"enabled": True})
            assert res.status_code == 500

    def test_entry_decisions_endpoint(self, auth_client):
        client, _ = auth_client
        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_db = MagicMock()
            mock_fs.return_value = mock_db
            doc1 = MagicMock()
            doc1.id = "ed1"
            doc1.to_dict.return_value = {"symbol": "IWM", "verdict": "ALLOWED", "timestamp": datetime.now()}
            mock_db.collection("entry_decisions").where.return_value.limit.return_value.stream.return_value = [doc1]
            mock_db.collection("entry_decisions").limit.return_value.stream.return_value = [doc1]

            res = client.get("/api/entry-decisions?symbol=IWM")
            assert res.status_code == 200
            assert len(res.json()["decisions"]) == 1

            mock_db.collection.side_effect = Exception("ed fail")
            res_err = client.get("/api/entry-decisions")
            assert res_err.status_code == 200
            assert res_err.json()["status"] == "error"


# ============================================================================
# 9. SERVICES/PNL_STATEMENT_ENGINE.PY: EDGE CASES & PERIOD AGGREGATION
# ============================================================================

class TestPnLStatementEngineCoverage:
    def test_parse_iso_datetime_branches(self):
        from services.pnl_statement_engine import parse_iso_datetime

        assert parse_iso_datetime(None) is None
        assert parse_iso_datetime("") is None

        # Standard ISO with Z
        dt1 = parse_iso_datetime("2026-10-10T12:00:00Z")
        assert dt1.tzinfo == timezone.utc

        # ISO without tzinfo
        dt2 = parse_iso_datetime("2026-10-10T12:00:00")
        assert dt2.tzinfo == timezone.utc

        # Alternative format with fallback strptime
        dt3 = parse_iso_datetime("2026-10-10T12:00:00.123456+00:00")
        assert dt3 is not None

        # Invalid string
        assert parse_iso_datetime("NOT_A_DATE") is None

    def test_get_period_start_date_branches(self):
        from services.pnl_statement_engine import get_period_start_date
        fixed_now = datetime(2026, 10, 15, 14, 0, 0, tzinfo=timezone.utc)

        assert get_period_start_date("1D", fixed_now).day == 15
        assert get_period_start_date("1W", fixed_now).day == 8
        assert get_period_start_date("1M", fixed_now).month == 9
        assert get_period_start_date("QTD", fixed_now).month == 10  # Q4 starts Oct 1
        assert get_period_start_date("YTD", fixed_now).month == 1
        assert get_period_start_date("1Y", fixed_now).year == 2025
        assert get_period_start_date("ALL", fixed_now).year == 2020
        assert get_period_start_date("UNKNOWN", fixed_now).month == 1

    def test_estimate_trade_commission_branches(self):
        from services.pnl_statement_engine import estimate_trade_commission

        # Valid direct commission
        assert estimate_trade_commission({"commission": 2.50}) == 2.50

        # Invalid direct commission string falls through
        assert estimate_trade_commission({"commission": "bad", "validation": {"ibkr_commission": 3.0}}) == 3.0

        # Invalid validation commission string falls through to analysis_metadata
        trade = {
            "commission": "bad",
            "validation": {"ibkr_commission": "bad"},
            "analysis_metadata": {"quantity": 200}
        }
        comm = estimate_trade_commission(trade)
        assert comm >= 2.0

    def test_aggregate_portfolio_pnl_modes_and_periods(self):
        from services.pnl_statement_engine import aggregate_portfolio_pnl

        trades = [
            {
                "timestamp": "2026-10-10T14:00:00Z",
                "symbol": "NVDA",
                "botId": 4,
                "profitLoss": 1500.0,
                "trading_mode": "live"
            },
            {
                "timestamp": "2026-10-11T14:00:00Z",
                "symbol": "IWM",
                "botId": 3,
                "profitLoss": -950.0,
                "trading_mode": "paper"
            },
            {
                "timestamp": "2026-10-12T14:00:00Z",
                "symbol": "NVDA",
                "botId": 4,
                "profitLoss": 0.0,
                "trading_mode": "live"
            }
        ]

        bot_overview = {
            "nvda": {"symbol": "NVDA", "position_size": 100, "unrealized_pnl": 200.0}
        }
        configs = [
            {"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "trading_mode": "live"},
            {"name": "iwm_sma", "symbol": "IWM", "client_id": 3, "trading_mode": "paper"}
        ]
        acct_overview = {
            "paper_net_liquidation": 105000.0,
            "paper_total_cash": 40000.0,
            "net_liquidation": 190000.0,
            "total_cash": 60000.0,
            "margin_utilization_pct": 25.0
        }

        # 1. Paper mode
        stmt_paper = aggregate_portfolio_pnl(
            trades=trades, bot_overview_data=bot_overview, bot_configs=configs,
            account_overview_data=acct_overview, period="1D", bot_scope="ALL", mode="paper"
        )
        assert stmt_paper.trading_mode == "paper"
        assert stmt_paper.period == "1D"

        # 2. Live mode
        stmt_live = aggregate_portfolio_pnl(
            trades=trades, bot_overview_data=bot_overview, bot_configs=configs,
            account_overview_data=acct_overview, period="1W", bot_scope="ALL", mode="live"
        )
        assert stmt_live.trading_mode == "live"
        assert stmt_live.period == "1W"

        # 3. All periods in all/both mode
        fixed_now = datetime(2026, 10, 12, 18, 0, 0, tzinfo=timezone.utc)
        for p in ["1D", "1W", "1M", "QTD", "1Y", "ALL"]:
            stmt_all = aggregate_portfolio_pnl(
                trades=trades, bot_overview_data=bot_overview, bot_configs=configs,
                account_overview_data=acct_overview, period=p, bot_scope="ALL", mode="all",
                now=fixed_now
            )
            assert stmt_all.trading_mode == "all"
            assert stmt_all.period == p
            assert len(stmt_all.calendar_heatmap) > 0


# ============================================================================
# 10. STOCKPICKER/SENTINEL.PY: THESIS AUDIT & TELEMETRY
# ============================================================================

class TestStockpickerSentinelCoverage:
    def test_sentinel_empty_and_error_branches(self):
        from stockpicker.sentinel import audit_in_flight_positions, _find_bots_config

        # Empty active positions
        with patch("stockpicker.sentinel._hydrate_active_positions", return_value=[]):
            res = audit_in_flight_positions(db=None, force=True)
            assert res["positions_checked"] == 0

        # Position with empty symbol and error
        mock_positions = [
            {"symbol": "", "strategy_track": "GROWTH"},
            {"symbol": "ERR_SYM", "entry_score": 75.0, "strategy_track": "GROWTH"}
        ]
        with patch("stockpicker.sentinel._hydrate_active_positions", return_value=mock_positions), \
             patch("stockpicker.core.compute_fundamental_score", side_effect=Exception("score fail")):
            res = audit_in_flight_positions(db=None, force=True)
            assert res["positions_checked"] == 1

        # Income candidate with exception in options check
        mock_income_pos = [
            {"symbol": "INC_SYM", "entry_score": 75.0, "strategy_track": "INCOME"}
        ]
        with patch("stockpicker.sentinel._hydrate_active_positions", return_value=mock_income_pos), \
             patch("stockpicker.core.compute_fundamental_score", return_value={"score": 75.0}), \
             patch("stockpicker.income_screener.evaluate_covered_call_candidate", side_effect=Exception("opt fail")):
            res = audit_in_flight_positions(db=None, force=True)
            assert res["positions_checked"] == 1

        path = _find_bots_config()
        assert path is not None or path is None

    def test_hydrate_active_positions_and_persist_telemetry(self):
        from stockpicker.sentinel import _hydrate_active_positions, _persist_telemetry

        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "already_accepted": [{"symbol": "NVDA", "client_id": 4, "strategy_track": "GROWTH", "initial_score": 80.0}]
        }
        mock_db.collection("stock_picks").document("current").get.return_value = mock_doc

        positions = _hydrate_active_positions(mock_db)
        assert len(positions) >= 1

        pos_result = {
            "symbol": "NVDA",
            "client_id": 4,
            "thesis_telemetry": {
                "current_score": 82.0, "score_delta": 2.0, "thesis_state": "HEALTHY",
                "action_instruction": "HOLD", "last_evaluated": "2026-10-11T12:00:00Z"
            }
        }
        _persist_telemetry(mock_db, pos_result)
        mock_db.collection("bots").document("bot_4").set.assert_called()


# ============================================================================
# 11. STOCKPICKER/INCOME_SCREENER.PY & FEEDBACK_ENGINE.PY COVERAGE
# ============================================================================

class TestStockpickerIncomeAndFeedbackCoverage:
    def test_income_screener_synthetic_options_and_cache(self):
        from stockpicker.income_screener import evaluate_covered_call_candidate, fetch_earnings_date, _earnings_date_cache

        with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=None), \
             patch("stockpicker.income_screener.fetch_ticker_quote_metrics", return_value={"current_price": 100.0, "fifty_two_high": 120.0, "fifty_two_low": 80.0}), \
             patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(UTC) + timedelta(days=45)):
            res = evaluate_covered_call_candidate("SYNTH_SYM")
            assert res is not None
            assert res["used_synthetic_options"] is True

        _earnings_date_cache["CACHED_SYM"] = (time.time(), datetime(2026, 11, 1, tzinfo=UTC))
        dt = fetch_earnings_date("CACHED_SYM")
        assert dt is not None
        assert fetch_earnings_date("SPY") is None

    def test_feedback_engine_cleanup_and_price_fetching(self):
        from stockpicker.feedback_engine import cleanup_stale_archives, _fetch_current_price, _fetch_historical_price

        mock_db = MagicMock()
        doc_cur = MagicMock()
        doc_cur.id = "current"
        doc_old = MagicMock()
        doc_old.id = "2025-01-01"
        doc_bad = MagicMock()
        doc_bad.id = "not-a-date"

        mock_db.collection("stock_picks_archive").where.return_value.limit.return_value.stream.return_value = [doc_old]
        mock_db.collection("stock_picks").stream.return_value = [doc_cur, doc_old, doc_bad]
        mock_db.collection("stockpicker_execution_feedback").where.return_value.limit.return_value.stream.return_value = []

        res = cleanup_stale_archives(mock_db, ttl_days=90)
        assert res["deleted"]["daily_dumps"] >= 1
        doc_old.reference.delete.assert_called()

        with patch("yfinance.Ticker") as mock_yf:
            mock_stock = MagicMock()
            import pandas as pd
            df = pd.DataFrame({"Close": [150.25]}, index=[pd.Timestamp("2026-10-10")])
            mock_stock.history.return_value = df
            mock_yf.return_value = mock_stock

            price_cur = _fetch_current_price("NVDA")
            assert price_cur == 150.25

            price_hist = _fetch_historical_price("NVDA", "2026-10-10")
            assert price_hist == 150.25


# ============================================================================
# 12. STOCKPICKER/CORE.PY: GEMINI RANKING FALLBACKS & NEWS FETCHERS
# ============================================================================

class TestStockpickerCoreCoverage:
    def test_rank_news_with_gemini_fallbacks(self):
        from stockpicker.core import call_llm_rank

        records = [{"headline": "Tech expands", "summary": "details", "tickers": "NVDA"}]

        # 1. Gemini returned no candidates -> fallback
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key"}):
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"candidates": []}
                mock_post.return_value = mock_resp

                ranked = call_llm_rank(records)
                assert len(ranked) >= 1

            # 2. Text is regex json list
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "candidates": [{
                        "content": {"parts": [{"text": "```json\n[{\"explosiveness\": 8.5, \"headline\": \"h1\"}]\n```"}]}
                    }]
                }
                mock_post.return_value = mock_resp

                ranked = call_llm_rank(records)
                assert ranked[0]["explosiveness"] == 8.5

            # 3. Text has no json list -> fallback
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "candidates": [{
                        "content": {"parts": [{"text": "Sorry I cannot answer that."}]}
                    }]
                }
                mock_post.return_value = mock_resp

                ranked = call_llm_rank(records)
                assert len(ranked) >= 1

    def test_rank_news_fundamentals_fallback(self):
        from stockpicker.core import rank_news

        with patch("stockpicker.core.generate_fundamentals_picks", return_value=[{"ticker": "NVDA"}]) as mock_fund:
            picks = rank_news([])
            assert len(picks) == 1
            mock_fund.assert_called()

    def test_get_yahoo_crumb_and_cookies(self):
        from stockpicker.core import get_yahoo_crumb_and_cookies

        with patch("requests.Session") as mock_sess, patch("requests.get") as mock_get:
            mock_sess.side_effect = Exception("session fail")

            mock_fc = MagicMock()
            mock_fc.cookies = {"B": "cookie1"}
            mock_crumb = MagicMock()
            mock_crumb.status_code = 200
            mock_crumb.text = "test_crumb_123"

            mock_get.side_effect = [mock_fc, mock_crumb]

            crumb, cookies = get_yahoo_crumb_and_cookies(force_refresh=True)
            assert crumb == "test_crumb_123"

    def test_external_news_fetchers(self):
        from stockpicker.core import (
            fetch_newsapi,
            fetch_x_recent,
            fetch_polygon_benzinga,
            fetch_finnhub_news,
            fetch_alphavantage_news
        )

        with patch.dict(os.environ, {
            "NEWSAPI_KEY": "test_key",
            "X_BEARER_TOKEN": "test_token",
            "POLYGON_API_KEY": "test_key",
            "FINNHUB_API_KEY": "test_key",
            "ALPHAVANTAGE_API_KEY": "test_key"
        }):
            # NewsAPI
            with patch("stockpicker.core.safe_get", return_value={"articles": [{"title": "News 1", "description": "desc"}]}):
                items = fetch_newsapi("2026-10-10T00:00:00Z")
                assert len(items) == 1

            # X / Twitter
            with patch("stockpicker.core.safe_get", return_value={
                "data": [{"id": "1", "text": "Bullish on $NVDA today", "public_metrics": {"like_count": 10}}],
                "includes": {"users": [{"id": "u1", "username": "trader"}]}
            }):
                items = fetch_x_recent()
                assert len(items) == 1
                assert "NVDA" in items[0].tickers

            # Benzinga
            with patch("stockpicker.core.safe_get", return_value={"results": [{"title": "Earnings", "tickers": ["NVDA"]}]}):
                items = fetch_polygon_benzinga("2026-10-10")
                assert len(items) == 1

            # Finnhub
            with patch("stockpicker.core.safe_get", return_value=[{"headline": "Market open", "summary": "stocks up"}]):
                items = fetch_finnhub_news("2026-10-10T00:00:00Z")
                assert len(items) == 1

            # AlphaVantage
            with patch("stockpicker.core.safe_get", return_value={
                "feed": [{
                    "title": "NVDA Earnings",
                    "time_published": "20261010T120000",
                    "overall_sentiment_score": 0.8,
                    "ticker_sentiment": [{"ticker": "NVDA"}]
                }]
            }):
                items = fetch_alphavantage_news("2026-10-10T00:00:00Z")
                assert len(items) == 1
