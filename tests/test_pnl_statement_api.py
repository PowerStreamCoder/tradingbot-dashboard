"""
API tests for GET /api/pnl-statement in tradingbot-dashboard.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from main import app, authenticated_sessions
from services.pnl_statement_engine import clear_statement_cache


class TestPnLStatementAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        clear_statement_cache()
        # Set up authenticated session (naive datetime to match main.py)
        self.session_id = "test-pnl-session-789"
        authenticated_sessions[self.session_id] = datetime.now() + timedelta(hours=2)
        self.client.cookies.set("dashboard_session", self.session_id)

    def tearDown(self):
        authenticated_sessions.pop(self.session_id, None)

    @patch("main.get_all_trade_history")
    @patch("main.load_bot_configs")
    @patch("main.dashboard_data._load_bot_data")
    def test_get_pnl_statement_success(self, mock_load_bot, mock_configs, mock_trades):
        # Setup mocks
        mock_trades.return_value = {
            "trades": [
                {
                    "timestamp": "2026-10-01T14:30:00Z",
                    "symbol": "NVDA",
                    "botId": 4,
                    "profitLoss": 500.0,
                    "option_pnl": 100.0,
                    "commission": 2.0
                },
                {
                    "timestamp": "2026-10-02T15:30:00Z",
                    "symbol": "IWM",
                    "botId": 3,
                    "profitLoss": -150.0,
                    "option_pnl": 0.0,
                    "commission": 2.0
                }
            ]
        }
        mock_configs.return_value = {
            "bots": [
                {"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "strategy": "sma_crossover", "enabled": True},
                {"name": "iwm_sma", "symbol": "IWM", "client_id": 3, "strategy": "sma_momentum", "enabled": True}
            ]
        }
        mock_load_bot.return_value = {
            "nvda": {"symbol": "NVDA", "position_size": 100, "avg_cost": 120.0, "unrealized_pnl": 250.0}
        }

        # Request
        response = self.client.get("/api/pnl-statement?period=YTD&bot=ALL")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["period"], "YTD")
        self.assertEqual(data["bot_scope"], "ALL")
        self.assertIn("executive_kpis", data)
        self.assertIn("cashflow_waterfall", data)
        self.assertIn("bot_attribution", data)
        self.assertIn("equity_curve", data)
        self.assertIn("calendar_heatmap", data)

        # Check total trades and realized P&L (500 - 150 = 350.0)
        self.assertEqual(data["executive_kpis"]["total_trades"], 2)
        self.assertEqual(data["executive_kpis"]["realized_pnl"], 350.0)

    @patch("main.get_all_trade_history")
    @patch("main.load_bot_configs")
    @patch("main.dashboard_data._load_bot_data")
    def test_pnl_statement_caching_header(self, mock_load_bot, mock_configs, mock_trades):
        mock_trades.return_value = {"trades": []}
        mock_configs.return_value = {"bots": []}
        mock_load_bot.return_value = {}

        # 1st call -> MISS
        res1 = self.client.get("/api/pnl-statement?period=1M")
        self.assertEqual(res1.status_code, 200)
        self.assertIn("MISS", res1.headers.get("X-Cache-Status", ""))

        # 2nd call -> HIT
        res2 = self.client.get("/api/pnl-statement?period=1M")
        self.assertEqual(res2.status_code, 200)
        self.assertIn("HIT", res2.headers.get("X-Cache-Status", ""))


if __name__ == '__main__':
    unittest.main()
