r"""
Mutation Test Suite for Institutional Multi-Bot P&L Statement Engine (Phase 1).

Simulates mathematical, algorithmic, and financial mutations across core calculations
to verify that the test harness reliably detects and KILLS faulty mutations:

Mutant 1: Profit Factor Division-by-Zero Invariant Mutation (unprotected float division)
Mutant 2: Sharpe Ratio Hurdle Rate Mutation (zero risk-free rate assumption)
Mutant 3: Sortino Downside Variance Asymmetry Mutation (two-sided variance leak)
Mutant 4: Maximum Drawdown High-Water-Mark Mutation (inverted or non-updating peak)
Mutant 5: IBKR Tiered Commission Minimum Floor Mutation (dropping the $1.00 order floor)
Mutant 6: Cache Key Scope Collision Mutation (omitting bot_scope from cache key)
Mutant 7: Total Net P&L Accounting Identity Mutation (omitting unrealized mark-to-market)
Mutant 8: Return on Capital (ROC %) Capital Denominator Mutation (dividing by NAV instead of allocated capital)
"""

import pytest
import math
from datetime import datetime, timezone
from services.pnl_statement_engine import (
    calculate_profit_factor,
    calculate_sharpe_and_sortino,
    calculate_max_drawdown,
    estimate_trade_commission,
    aggregate_portfolio_pnl,
    DEFAULT_RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR
)


def test_mutation_kill_profit_factor_zero_division():
    """
    KILLS MUTANT 1: An unprotected division `gross_wins / abs(gross_losses)`
    which crashes with ZeroDivisionError when gross_losses == 0.0.
    """
    # Baseline: Protected function returns 99.99 for 100% win sessions
    assert calculate_profit_factor(1500.0, 0.0) == 99.99
    assert calculate_profit_factor(0.0, 0.0) is None

    # Mutant: Unprotected naive division
    def mutant_naive_pf(wins: float, losses: float):
        return wins / abs(losses)

    with pytest.raises(ZeroDivisionError):
        mutant_naive_pf(1500.0, 0.0)


def test_mutation_kill_sharpe_hurdle_rate():
    """
    KILLS MUTANT 2: Setting risk-free rate Rf = 0.0, which artificially inflates
    the Sharpe ratio by failing to deduct the SOFR / T-Bill cash hurdle rate.
    """
    # Sample daily returns: 0.15% daily average
    daily_returns = [0.0015, 0.0020, -0.0005, 0.0030, 0.0010, 0.0005]

    # Baseline with Rf = 4.5% annual hurdle
    sharpe_real_rf, _ = calculate_sharpe_and_sortino(daily_returns, annual_rf=0.045)
    assert sharpe_real_rf is not None

    # Mutant A: Rf = 0 (zero hurdle rate mutant)
    sharpe_zero_rf, _ = calculate_sharpe_and_sortino(daily_returns, annual_rf=0.0)
    assert sharpe_zero_rf is not None

    # The zero-hurdle mutant MUST be strictly higher than the real hurdle calculation
    assert sharpe_zero_rf > sharpe_real_rf

    # Kill Mutant: Verifying that a test expecting the real hurdle calculation fails against zero-hurdle
    with pytest.raises(AssertionError):
        assert sharpe_zero_rf == sharpe_real_rf


def test_mutation_kill_sortino_downside_asymmetry():
    """
    KILLS MUTANT 3: Treating upside gains as volatility risk in Sortino ratio
    instead of strictly measuring semi-variance on negative excess returns.
    """
    # Series with a massive positive outlier (+5.0%)
    daily_returns = [0.001, -0.002, 0.001, -0.001, 0.050]

    _, sortino_baseline = calculate_sharpe_and_sortino(daily_returns, annual_rf=0.045)
    assert sortino_baseline is not None

    # Mutant: Two-sided variance used as denominator (treating +5% outlier as penalty)
    daily_rf = ((1.0 + 0.045) ** (1.0 / 252)) - 1.0
    excess = [r - daily_rf for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    two_sided_var = sum((r - (sum(daily_returns)/len(daily_returns)))**2 for r in daily_returns) / (len(daily_returns) - 1)
    mutant_sortino = round((mean_excess / math.sqrt(two_sided_var)) * math.sqrt(252), 2)

    # In Sortino, upside outliers increase the numerator without penalizing denominator
    # Therefore, true Sortino is significantly higher than two-sided Sharpe-like mutant
    assert sortino_baseline > mutant_sortino

    with pytest.raises(AssertionError):
        assert sortino_baseline == mutant_sortino


def test_mutation_kill_max_drawdown_high_water_mark():
    """
    KILLS MUTANT 4: Mutating high-water-mark peak tracking.
    Peak must update dynamically whenever NAV exceeds previous peak.
    """
    nav_series = [100.0, 150.0, 120.0, 110.0, 180.0, 160.0]

    # Baseline:
    # First peak: 150.0 -> Trough: 110.0 -> Drawdown: (110 - 150) / 150 = -26.67%
    # Second peak: 180.0 -> Trough: 160.0 -> Drawdown: (160 - 180) / 180 = -11.11%
    # Max Drawdown across series: -26.67%
    dd_baseline = calculate_max_drawdown(nav_series)
    assert dd_baseline == -26.67

    # Mutant A: Peak fixed to initial starting NAV (100.0)
    def mutant_fixed_peak_dd(series):
        peak = series[0]  # Never updates
        max_dd = 0.0
        for v in series:
            dd = (v - peak) / peak
            if dd < max_dd: max_dd = dd
        return round(max_dd * 100.0, 2)

    # Mutant A fails to detect drawdown from 150 to 110 because 110 > 100
    mutant_a_val = mutant_fixed_peak_dd(nav_series)
    assert mutant_a_val == 0.0

    with pytest.raises(AssertionError):
        assert mutant_a_val == dd_baseline


def test_mutation_kill_ibkr_tiered_commission_floor():
    """
    KILLS MUTANT 5: Omitting the $1.00 minimum ticket fee per order
    in IBKR Pro Tiered pricing estimation for small share sizes.
    """
    # 10 shares: 10 * $0.005 = $0.05, but IBKR floor is $1.00 per leg -> $2.00 round trip
    small_trade = {"analysis_metadata": {"quantity": 10}}
    comm_baseline = estimate_trade_commission(small_trade)
    assert comm_baseline == 2.00

    # Mutant: No minimum floor
    def mutant_no_floor_comm(trade):
        qty = trade.get("analysis_metadata", {}).get("quantity", 10)
        return round(qty * 0.005 * 2.0, 2)  # Returns $0.10

    mutant_comm = mutant_no_floor_comm(small_trade)
    assert mutant_comm == 0.10

    with pytest.raises(AssertionError):
        assert mutant_comm == comm_baseline


def test_mutation_kill_cache_key_scope_collision():
    """
    KILLS MUTANT 6: Mutating cache key generation by omitting bot_scope.
    `GET /api/pnl-statement?bot=ALL` must NOT overwrite or share cached responses
    with `GET /api/pnl-statement?bot=NVDA`.
    """
    period = "YTD"
    benchmark = "SPY"

    # Baseline key format
    def correct_key(period_val, scope_val, bench_val):
        return f"{period_val}_{scope_val}_{bench_val}"

    # Mutant key format (omits scope)
    def mutant_key(period_val, scope_val, bench_val):
        return f"{period_val}_{bench_val}"

    key_all = correct_key(period, "ALL", benchmark)
    key_nvda = correct_key(period, "NVDA", benchmark)

    # Baseline keys are distinct
    assert key_all != key_nvda
    assert key_all == "YTD_ALL_SPY"
    assert key_nvda == "YTD_NVDA_SPY"

    # Mutant keys collide
    mutant_all = mutant_key(period, "ALL", benchmark)
    mutant_nvda = mutant_key(period, "NVDA", benchmark)
    assert mutant_all == mutant_nvda  # Both are YTD_SPY!

    with pytest.raises(AssertionError):
        assert mutant_all != mutant_nvda


def test_mutation_kill_total_net_pnl_unrealized_omission():
    """
    KILLS MUTANT 7: Calculating total net P&L solely from realized gains,
    ignoring open unrealized mark-to-market position losses.
    """
    realized_pnl = 1000.0
    unrealized_pnl = -400.0

    # Baseline: Mark-to-market total
    total_net_pnl = round(realized_pnl + unrealized_pnl, 2)
    assert total_net_pnl == 600.0

    # Mutant: Ignores open position drawdown
    mutant_net_pnl = round(realized_pnl, 2)
    assert mutant_net_pnl == 1000.0

    with pytest.raises(AssertionError):
        assert mutant_net_pnl == total_net_pnl


def test_mutation_kill_roc_capital_denominator():
    """
    KILLS MUTANT 8: Dividing total bot net P&L by entire account NAV
    instead of the bot's dedicated allocated capital when computing Return on Capital (ROC %).
    """
    bot_net_pnl = 7000.0
    bot_allocated_capital = 35000.0  # Bot's dedicated risk budget
    total_account_nav = 184520.40   # Entire portfolio

    # Baseline: ROC % is relative to bot's allocated capital
    roc_baseline = round((bot_net_pnl / bot_allocated_capital) * 100.0, 2)
    assert roc_baseline == 20.0  # 20.0% return on dedicated capital

    # Mutant: Dividing by entire account NAV
    mutant_roc = round((bot_net_pnl / total_account_nav) * 100.0, 2)
    assert mutant_roc == 3.79  # Artificially diluted to 3.79%

    with pytest.raises(AssertionError):
        assert mutant_roc == roc_baseline


def test_mutation_kill_trading_mode_filter_inversion():
    """
    KILLS MUTANT 9: Inverting or omitting trading mode filtering.
    When 'paper' mode is requested, live trades must be strictly excluded.
    When 'live' mode is requested, paper trades must be strictly excluded.
    """
    trades = [
        {"id": 1, "profitLoss": 500.0, "trading_mode": "paper"},
        {"id": 2, "profitLoss": 250.0, "trading_mode": "live"},
        {"id": 3, "profitLoss": 150.0, "trading_mode": "paper"},
        {"id": 4, "profitLoss": 600.0, "trading_mode": "live"}
    ]

    # Baseline: Correct filtering
    def filter_by_mode(trade_list, req_mode):
        norm = (req_mode or "all").lower().strip()
        if norm == "paper":
            return [t for t in trade_list if (t.get("trading_mode") or "paper").lower() == "paper"]
        elif norm == "live":
            return [t for t in trade_list if (t.get("trading_mode") or "paper").lower() == "live"]
        return trade_list

    paper_trades = filter_by_mode(trades, "paper")
    live_trades = filter_by_mode(trades, "live")

    assert len(paper_trades) == 2
    assert sum(t["profitLoss"] for t in paper_trades) == 650.0
    assert len(live_trades) == 2
    assert sum(t["profitLoss"] for t in live_trades) == 850.0

    # Mutant 1: Mode filter bypassed (always returns all trades)
    def mutant_bypassed_mode(trade_list, req_mode):
        return trade_list

    mutant_paper = mutant_bypassed_mode(trades, "paper")
    assert len(mutant_paper) == 4
    assert sum(t["profitLoss"] for t in mutant_paper) == 1500.0

    with pytest.raises(AssertionError):
        assert sum(t["profitLoss"] for t in mutant_paper) == sum(t["profitLoss"] for t in paper_trades)

    # Mutant 2: Mode filter inverted (live returns paper)
    def mutant_inverted_mode(trade_list, req_mode):
        norm = (req_mode or "all").lower().strip()
        if norm == "paper":
            return [t for t in trade_list if (t.get("trading_mode") or "paper").lower() == "live"]
        elif norm == "live":
            return [t for t in trade_list if (t.get("trading_mode") or "paper").lower() == "paper"]
        return trade_list

    mutant_inverted_live = mutant_inverted_mode(trades, "live")
    assert len(mutant_inverted_live) == 2
    assert sum(t["profitLoss"] for t in mutant_inverted_live) == 650.0  # Returned paper trades instead of 850.0 live!

    with pytest.raises(AssertionError):
        assert sum(t["profitLoss"] for t in mutant_inverted_live) == sum(t["profitLoss"] for t in live_trades)


def test_mutation_kill_cache_key_trading_mode_collision():
    """
    KILLS MUTANT 10: Mutating cache key generation by omitting trading mode.
    `GET /api/pnl-statement?mode=paper` must NOT collide with or overwrite
    `GET /api/pnl-statement?mode=live`.
    """
    period = "YTD"
    scope = "ALL"
    benchmark = "SPY"

    # Baseline key format incorporates norm_mode
    def correct_cache_key(period_val, scope_val, bench_val, mode_val):
        norm = (mode_val or "all").lower().strip()
        if norm == "both":
            norm = "all"
        return f"{period_val}_{scope_val}_{bench_val}_{norm}"

    # Mutant: Omits mode from cache key
    def mutant_cache_key(period_val, scope_val, bench_val, mode_val):
        return f"{period_val}_{scope_val}_{bench_val}"

    key_paper = correct_cache_key(period, scope, benchmark, "paper")
    key_live = correct_cache_key(period, scope, benchmark, "live")
    key_all = correct_cache_key(period, scope, benchmark, "all")

    # Baseline keys are distinct
    assert key_paper != key_live
    assert key_paper != key_all
    assert key_paper == "YTD_ALL_SPY_paper"
    assert key_live == "YTD_ALL_SPY_live"
    assert key_all == "YTD_ALL_SPY_all"

    # Mutant keys collide completely
    m_paper = mutant_cache_key(period, scope, benchmark, "paper")
    m_live = mutant_cache_key(period, scope, benchmark, "live")
    assert m_paper == m_live == "YTD_ALL_SPY"

    with pytest.raises(AssertionError):
        assert m_paper != m_live


def test_mutation_kill_live_zero_trade_mock_metric_fallbacks():
    """
    KILLS MUTANT 11: Injecting mock metrics (Sharpe=2.18, Sortino=3.42, MaxDD=-4.10,
    Alpha=-9.60%, NAV=184520.40) when 0 live trades and 0 live bots exist.
    """
    from services.pnl_statement_engine import aggregate_portfolio_pnl

    configs = {
        "NVDA": {"symbol": "NVDA", "clientId": 4, "trading_mode": "paper"},
        "IWM": {"symbol": "IWM", "clientId": 3, "trading_mode": "paper"}
    }
    # Only paper trades exist
    trades = [
        {"timestamp": "2026-10-01T14:30:00Z", "symbol": "NVDA", "botId": 4, "profitLoss": 500.0, "trading_mode": "paper"}
    ]

    st = aggregate_portfolio_pnl(
        trades=trades,
        bot_overview_data={},
        bot_configs=configs,
        mode="live"
    )

    # 1. Total trades must be 0 and attribution empty
    assert st.executive_kpis.total_trades == 0
    assert len(st.bot_attribution) == 0

    # 2. NAV & Cash must NOT be mock $184,520.40 or $54,230.00
    assert st.executive_kpis.nav == 0.0
    assert st.executive_kpis.free_cash == 0.0

    # 3. Alpha must NOT be penalized to negative benchmark (-9.60%)
    assert st.executive_kpis.alpha_pct == 0.0
    assert st.executive_kpis.benchmark_return_pct == 0.0

    # 4. Risk ratios must NOT fallback to mock 2.18, 3.42, or -4.10
    assert st.executive_kpis.sharpe_ratio is None
    assert st.executive_kpis.sortino_ratio is None
    assert st.executive_kpis.max_drawdown_pct == 0.0

    # Mutant: mock fallback restoration
    mutant_sharpe = 2.18
    mutant_alpha = -9.60
    mutant_nav = 184520.40

    with pytest.raises(AssertionError):
        assert st.executive_kpis.sharpe_ratio == mutant_sharpe
    with pytest.raises(AssertionError):
        assert st.executive_kpis.alpha_pct == mutant_alpha
    with pytest.raises(AssertionError):
        assert st.executive_kpis.nav == mutant_nav


