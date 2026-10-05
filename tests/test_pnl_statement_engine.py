"""
Unit tests for the Institutional Multi-Bot P&L Statement Engine.
"""

import unittest
from datetime import datetime, timezone, timedelta
from services.pnl_statement_engine import (
    aggregate_portfolio_pnl,
    calculate_profit_factor,
    calculate_sharpe_and_sortino,
    calculate_max_drawdown,
    estimate_trade_commission,
    get_period_start_date,
    clear_statement_cache,
    PortfolioStatementResponse
)


class TestPnLStatementEngine(unittest.TestCase):
    def setUp(self):
        clear_statement_cache()
        self.now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
        self.mock_configs = [
            {"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 70000.0},
            {"name": "iwm_sma", "symbol": "IWM", "client_id": 3, "strategy": "sma_momentum", "enabled": True, "allocated_capital": 46000.0},
            {"name": "spy_reversion", "symbol": "SPY", "client_id": 5, "strategy": "mean_reversion", "enabled": True, "allocated_capital": 36000.0}
        ]
        self.mock_bot_overview = {
            "nvda": {"symbol": "NVDA", "position_size": 100, "avg_cost": 120.0, "unrealized_pnl": 500.0},
            "iwm": {"symbol": "IWM", "position_size": 50, "avg_cost": 210.0, "unrealized_pnl": -150.0}
        }
        self.mock_trades = [
            {
                "timestamp": "2026-10-01T14:30:00Z",
                "symbol": "NVDA",
                "botId": 4,
                "profitLoss": 850.0,
                "option_pnl": 150.0,
                "commission": 2.50,
                "analysis_metadata": {"quantity": 100}
            },
            {
                "timestamp": "2026-10-02T15:00:00Z",
                "symbol": "NVDA",
                "botId": 4,
                "profitLoss": -200.0,
                "option_pnl": 50.0,
                "commission": 2.50,
                "analysis_metadata": {"quantity": 100}
            },
            {
                "timestamp": "2026-10-03T16:00:00Z",
                "symbol": "IWM",
                "botId": 3,
                "profitLoss": 420.0,
                "option_pnl": 0.0,
                "analysis_metadata": {"quantity": 50}
            }
        ]

    def test_profit_factor_calculation(self):
        # Normal
        self.assertEqual(calculate_profit_factor(1000.0, -500.0), 2.0)
        # Division by zero: 100% win rate
        self.assertEqual(calculate_profit_factor(1000.0, 0.0), 99.99)
        # No wins or losses
        self.assertIsNone(calculate_profit_factor(0.0, 0.0))

    def test_commission_estimation(self):
        # Explicit commission
        trade_with_comm = {"commission": 3.75}
        self.assertEqual(estimate_trade_commission(trade_with_comm), 3.75)

        # Fallback estimation: quantity = 500 -> 500 * 0.005 = 2.50 each way -> 5.00
        trade_est = {"analysis_metadata": {"quantity": 500}}
        self.assertEqual(estimate_trade_commission(trade_est), 5.00)

        # Min commission check: quantity = 10 -> $1.00 each way -> $2.00
        trade_min = {"analysis_metadata": {"quantity": 10}}
        self.assertEqual(estimate_trade_commission(trade_min), 2.00)

    def test_drawdown_calculation(self):
        nav_series = [100.0, 110.0, 105.0, 95.0, 100.0]
        # Peak is 110.0, trough is 95.0 -> (95 - 110) / 110 = -15 / 110 = -13.64%
        dd = calculate_max_drawdown(nav_series)
        self.assertEqual(dd, -13.64)

    def test_period_start_date(self):
        d_1d = get_period_start_date("1D", self.now)
        self.assertEqual(d_1d.day, self.now.day)
        self.assertEqual(d_1d.hour, 0)

        d_ytd = get_period_start_date("YTD", self.now)
        self.assertEqual(d_ytd.month, 1)
        self.assertEqual(d_ytd.day, 1)

    def test_aggregation_flow(self):
        statement = aggregate_portfolio_pnl(
            trades=self.mock_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            period="YTD",
            bot_scope="ALL",
            now=self.now
        )

        self.assertIsInstance(statement, PortfolioStatementResponse)
        self.assertEqual(statement.period, "YTD")
        self.assertEqual(statement.executive_kpis.total_trades, 3)

        # Realized PnL: 850 - 200 + 420 = 1070.0
        self.assertEqual(statement.executive_kpis.realized_pnl, 1070.0)

        # Unrealized PnL: 500 - 150 = 350.0
        self.assertEqual(statement.executive_kpis.unrealized_pnl, 350.0)

        # Total Net PnL = 1070 + 350 = 1420.0
        self.assertEqual(statement.executive_kpis.total_net_pnl, 1420.0)

        # Bot attribution records exist
        self.assertTrue(len(statement.bot_attribution) >= 2)
        nvda_bot = next(b for b in statement.bot_attribution if b.symbol == "NVDA")
        self.assertEqual(nvda_bot.trades_count, 2)
        self.assertEqual(nvda_bot.winning_trades, 1)
        self.assertEqual(nvda_bot.losing_trades, 1)
        self.assertEqual(nvda_bot.win_rate_pct, 50.0)

    def test_single_bot_scope(self):
        statement = aggregate_portfolio_pnl(
            trades=self.mock_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            period="YTD",
            bot_scope="NVDA",
            now=self.now
        )
        self.assertEqual(len(statement.bot_attribution), 1)
        self.assertEqual(statement.bot_attribution[0].symbol, "NVDA")

    def test_caching_behavior(self):
        # First call: cache miss
        st1 = aggregate_portfolio_pnl(
            trades=self.mock_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            now=self.now
        )
        self.assertFalse(st1.data_quality.cached)

        # Second call within TTL: cache hit
        now_later = self.now + timedelta(seconds=5)
        st2 = aggregate_portfolio_pnl(
            trades=self.mock_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            now=now_later
        )
        self.assertTrue(st2.data_quality.cached)
        self.assertEqual(st2.data_quality.cache_age_seconds, 5.0)

    def test_trading_mode_segmentation(self):
        # Setup trades with mixed paper and live modes
        mixed_trades = [
            {"timestamp": "2026-10-01T14:30:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 500.0, "trading_mode": "paper"},
            {"timestamp": "2026-10-02T14:30:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 300.0, "trading_mode": "live"},
            {"timestamp": "2026-10-03T14:30:00Z", "symbol": "IWM", "botId": 3, "profitLoss": -100.0, "trading_mode": "paper"},
            {"timestamp": "2026-10-04T14:30:00Z", "symbol": "IWM", "botId": 3, "profitLoss": 250.0, "trading_mode": "live"}
        ]

        # 1. Paper Mode: only paper trades (NVDA +500, IWM -100) -> 2 trades, realized = 400.0
        st_paper = aggregate_portfolio_pnl(
            trades=mixed_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            period="YTD",
            bot_scope="ALL",
            mode="paper",
            now=self.now
        )
        self.assertEqual(st_paper.trading_mode, "paper")
        self.assertEqual(st_paper.executive_kpis.total_trades, 2)
        self.assertEqual(st_paper.executive_kpis.realized_pnl, 400.0)
        self.assertEqual(st_paper.executive_kpis.nav, 100000.00)

        # 2. Live Mode: only live trades (NVDA +300, IWM +250) -> 2 trades, realized = 550.0
        st_live = aggregate_portfolio_pnl(
            trades=mixed_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            period="YTD",
            bot_scope="ALL",
            mode="live",
            now=self.now
        )
        self.assertEqual(st_live.trading_mode, "live")
        self.assertEqual(st_live.executive_kpis.total_trades, 2)
        self.assertEqual(st_live.executive_kpis.realized_pnl, 550.0)
        self.assertEqual(st_live.executive_kpis.nav, 184520.40)

        # 3. All / Both Mode: all 4 trades -> realized = 950.0
        st_all = aggregate_portfolio_pnl(
            trades=mixed_trades,
            bot_overview_data=self.mock_bot_overview,
            bot_configs=self.mock_configs,
            period="YTD",
            bot_scope="ALL",
            mode="all",
            now=self.now
        )
        self.assertEqual(st_all.trading_mode, "all")
        self.assertEqual(st_all.executive_kpis.total_trades, 4)
        self.assertEqual(st_all.executive_kpis.realized_pnl, 950.0)
        self.assertEqual(st_all.executive_kpis.nav, 284520.40)

        # Verify bot execution mode tagging
        nvda_bot = next(b for b in st_all.bot_attribution if b.symbol == "NVDA")
        self.assertEqual(nvda_bot.trading_mode, "BOTH")

    def test_trading_mode_cache_isolation(self):
        mixed_trades = [
            {"timestamp": "2026-10-01T14:30:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 500.0, "trading_mode": "paper"},
            {"timestamp": "2026-10-02T14:30:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 300.0, "trading_mode": "live"}
        ]
        # Query paper mode
        p1 = aggregate_portfolio_pnl(trades=mixed_trades, bot_overview_data={}, bot_configs=self.mock_configs, mode="paper", now=self.now)
        # Query live mode immediately after
        l1 = aggregate_portfolio_pnl(trades=mixed_trades, bot_overview_data={}, bot_configs=self.mock_configs, mode="live", now=self.now)
        # Must be different data, not a cache collision
        self.assertEqual(p1.trading_mode, "paper")
        self.assertEqual(l1.trading_mode, "live")
        self.assertEqual(p1.executive_kpis.realized_pnl, 500.0)
        self.assertEqual(l1.executive_kpis.realized_pnl, 300.0)
        self.assertFalse(l1.data_quality.cached)

    def test_live_mode_zero_state(self):
        # Scenario matching current production: trades exist but only in paper mode
        paper_only_trades = [
            {"timestamp": "2026-10-01T14:30:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 500.0, "trading_mode": "paper"},
            {"timestamp": "2026-10-02T14:30:00Z", "symbol": "IWM", "botId": 3, "profitLoss": -100.0, "trading_mode": "paper"}
        ]

        st_live = aggregate_portfolio_pnl(
            trades=paper_only_trades,
            bot_overview_data={},
            bot_configs=self.mock_configs,
            period="YTD",
            bot_scope="ALL",
            mode="live",
            now=self.now
        )

        self.assertEqual(st_live.trading_mode, "live")
        self.assertEqual(len(st_live.bot_attribution), 0)
        self.assertEqual(st_live.executive_kpis.total_trades, 0)
        self.assertEqual(st_live.executive_kpis.realized_pnl, 0.0)
        self.assertEqual(st_live.executive_kpis.unrealized_pnl, 0.0)
        self.assertEqual(st_live.executive_kpis.total_net_pnl, 0.0)
        self.assertEqual(st_live.executive_kpis.nav, 0.0)
        self.assertEqual(st_live.executive_kpis.starting_nav, 0.0)
        self.assertEqual(st_live.executive_kpis.free_cash, 0.0)
        self.assertEqual(st_live.executive_kpis.margin_utilization_pct, 0.0)
        self.assertEqual(st_live.executive_kpis.cash_buffer_pct, 0.0)
        self.assertEqual(st_live.executive_kpis.alpha_pct, 0.0)
        self.assertEqual(st_live.executive_kpis.benchmark_return_pct, 0.0)
        self.assertIsNone(st_live.executive_kpis.sharpe_ratio)
        self.assertIsNone(st_live.executive_kpis.sortino_ratio)
        self.assertEqual(st_live.executive_kpis.max_drawdown_pct, 0.0)
        self.assertEqual(st_live.cashflow_waterfall.margin_interest_paid, 0.0)
        self.assertEqual(st_live.cashflow_waterfall.cash_yield_earned, 0.0)
        self.assertTrue(all(pt == 0.0 for pt in st_live.equity_curve.portfolio_returns_pct))
        self.assertTrue(all(pt == 0.0 for pt in st_live.equity_curve.benchmark_returns_pct))
        self.assertEqual(st_live.risk_insights[0].title, "No Live Trading Bots Active")


if __name__ == '__main__':
    unittest.main()

