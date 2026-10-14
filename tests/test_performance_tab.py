"""
Tests for Performance Dashboard Tab and Performance Engine (v2.0.0)

Validates:
- Services: scoring, measured execution metrics, sample-size governance,
  public benchmarks, diagnostics
- Guarantees no fabricated metrics: values are None / "N/A" when unmeasurable
- API endpoints: GET /performance (HTML), GET /api/engine-performance (JSON)
- main.py helpers: period lookback mapping + real benchmark price series fetch
- Edge cases: empty trades, mode/period filters, caching immutability, errors
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

import main
from services import performance_engine as pe
from services.performance_engine import (
    compute_grade_from_score,
    compute_sample_confidence,
    score_execution_quality,
    score_risk_adjusted_returns,
    score_expectancy_and_edge,
    score_capital_preservation,
    compute_annualized_return_pct,
    compute_annualized_volatility_pct,
    _extract_slippage,
    _extract_quantity,
    _is_fill_confirmed,
    _first_present,
    _format_value,
    _compare_status,
    calculate_engine_performance,
    EnginePerformanceResponse,
)
from main import app, authenticated_sessions

FIXED_NOW = datetime(2026, 10, 13, 18, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_caches():
    """Isolate module-level caches between tests."""
    with pe._perf_cache_lock:
        pe._perf_cache.clear()
    with main._benchmark_series_lock:
        main._benchmark_series_cache.clear()
    with main._trade_history_cache['lock']:
        main._trade_history_cache['data'].clear()
        main._trade_history_cache['timestamp'].clear()
        main._trade_history_cache['ttl'].clear()
    yield
    with pe._perf_cache_lock:
        pe._perf_cache.clear()
    with main._benchmark_series_lock:
        main._benchmark_series_cache.clear()
    with main._trade_history_cache['lock']:
        main._trade_history_cache['data'].clear()
        main._trade_history_cache['timestamp'].clear()
        main._trade_history_cache['ttl'].clear()


@pytest.fixture
def test_client():
    client = TestClient(app)
    session_id = "test-perf-session-123"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=2)
    client.cookies.set("dashboard_session", session_id)
    yield client
    authenticated_sessions.pop(session_id, None)


@pytest.fixture
def sample_trades():
    return [
        {
            "timestamp": "2026-10-12T14:30:00Z",
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": 1250.0,
            "trading_mode": "live",
            "slippage": 0.015,
            "commission": 2.50,
            "analysis_metadata": {"quantity": 50},
        },
        {
            "timestamp": "2026-10-11T15:00:00Z",
            "symbol": "IWM",
            "botId": 3,
            "profitLoss": -450.0,
            "trading_mode": "paper",
            "slippage": 0.02,
            "commission": 2.00,
            "analysis_metadata": {"quantity": 100},
        },
        {
            "timestamp": "2026-10-10T16:00:00Z",
            "symbol": "MSFT",
            "botId": 5,
            "profitLoss": 800.0,
            "trading_mode": "live",
            "validation": {"slippage": 0.01, "ibkr_commission": 1.50},
            "analysis_metadata": {"quantity": 30},
        },
        {
            "timestamp": "2026-10-09T17:00:00Z",
            "symbol": "CAT",
            "botId": 6,
            "profitLoss": 350.0,
            "trading_mode": "paper",
        },
    ]


@pytest.fixture
def sample_bot_configs():
    return [
        {"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 60000.0},
        {"name": "iwm_sma", "symbol": "IWM", "client_id": 3, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 50000.0},
        {"name": "msft_sma", "symbol": "MSFT", "client_id": 5, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 60000.0},
        {"name": "cat_sma", "symbol": "CAT", "client_id": 6, "strategy": "sma_crossover", "enabled": False, "allocated_capital": 40000.0},
    ]


def _make_adequate_trades(symbol="NVDA", n=40, days=30, base=FIXED_NOW, unconfirmed_every=0,
                          mismatched_every=0, include_slippage=True):
    """Build an adequate sample (>=10 trades across >=10 days) for grading tests."""
    trades = []
    start = base - timedelta(days=days - 1)
    for i in range(n):
        day = start + timedelta(days=(i * (days - 1)) // max(1, n - 1))
        trade = {
            "timestamp": day.isoformat(),
            "symbol": symbol,
            "profitLoss": 300.0 if i % 3 else -150.0,
            "trading_mode": "live",
            "commission": 2.0,
            "analysis_metadata": {"quantity": 100},
        }
        if include_slippage:
            trade["slippage"] = 0.01
        if unconfirmed_every and i % unconfirmed_every == 0:
            trade["unconfirmedFill"] = True
            trade["exitPriceSource"] = "signal_fallback"
        if mismatched_every:
            trade["validation"] = {"validated": True, "matches": (i % mismatched_every != 0)}
        trades.append(trade)
    return trades


# ---------------------------------------------------------------------------
# Pure helper / scoring unit tests
# ---------------------------------------------------------------------------

def test_compute_grade_from_score():
    assert compute_grade_from_score(95) == "A+"
    assert compute_grade_from_score(90) == "A"
    assert compute_grade_from_score(85) == "A-"
    assert compute_grade_from_score(80) == "B+"
    assert compute_grade_from_score(75) == "B"
    assert compute_grade_from_score(70) == "B-"
    assert compute_grade_from_score(62) == "C+"
    assert compute_grade_from_score(55) == "C"
    assert compute_grade_from_score(40) == "D"


def test_compute_sample_confidence():
    assert compute_sample_confidence(0, 0) == "INSUFFICIENT"
    assert compute_sample_confidence(9, 30) == "INSUFFICIENT"
    assert compute_sample_confidence(50, 9) == "INSUFFICIENT"
    assert compute_sample_confidence(15, 15) == "LOW"
    assert compute_sample_confidence(40, 15) == "LOW"
    assert compute_sample_confidence(40, 25) == "ADEQUATE"


def test_score_execution_quality_measured_and_missing():
    assert score_execution_quality(99.0, 100.0, 8.0) >= 95
    mid = score_execution_quality(96.0, 92.0, 15.0)
    assert 60 <= mid <= 95
    low = score_execution_quality(80.0, 80.0, 35.0)
    assert low < 60
    # No measurable component -> None (never a fabricated score)
    assert score_execution_quality(None, None, None) is None
    # Partial: only cost drag available
    assert score_execution_quality(None, None, 8.0) == 100


def test_score_risk_adjusted_returns():
    assert score_risk_adjusted_returns(2.2, 2.8, 2.5) >= 90
    mid = score_risk_adjusted_returns(1.2, 1.5, 1.2)
    assert 70 <= mid < 90
    low = score_risk_adjusted_returns(0.2, 0.3, 0.1)
    assert low < 50
    # Unmeasurable -> None, not a default substitute
    assert score_risk_adjusted_returns(None, None, None) is None
    # Partial availability renormalizes
    assert score_risk_adjusted_returns(2.2, None, None) == 100


def test_score_expectancy_and_edge():
    assert score_expectancy_and_edge(2.2, 65.0, 1.5, 60.0) >= 90
    mid = score_expectancy_and_edge(1.4, 50.0, 1.1, 10.0)
    assert 60 <= mid < 90
    low = score_expectancy_and_edge(0.8, 35.0, 0.7, -25.0)
    assert low < 50
    assert score_expectancy_and_edge(None, 50.0, None, -30.0) is not None
    assert score_expectancy_and_edge(2.0, 50.0, 2.5, 45.0) >= 80


def test_score_capital_preservation():
    assert score_capital_preservation(-3.0, 65.0) >= 90
    mid = score_capital_preservation(-11.0, 25.0)
    assert 60 <= mid < 90
    low = score_capital_preservation(-26.0, -10.0)
    assert low < 45
    assert score_capital_preservation(None, None) is None
    assert score_capital_preservation(-5.0, None) == 90


def test_compute_annualized_return_and_volatility():
    assert compute_annualized_return_pct([]) is None
    assert compute_annualized_return_pct([-2.0]) is None  # non-positive growth
    ret = compute_annualized_return_pct([0.001] * 30)
    assert ret is not None and ret > 0
    assert compute_annualized_volatility_pct([0.01]) is None  # needs >= 2
    assert compute_annualized_volatility_pct([0.01, 0.01, 0.01]) is None  # zero variance
    vol = compute_annualized_volatility_pct([0.01, -0.02, 0.015, 0.0])
    assert vol is not None and vol > 0


def test_extract_slippage_variants():
    assert _extract_slippage({"slippage": 0.05}) == 0.05
    assert _extract_slippage({"slippage": -0.05}) == 0.05
    assert _extract_slippage({"validation": {"slippage": 0.02}}) == 0.02
    assert _extract_slippage({"slippage": "nope"}) is None
    assert _extract_slippage({}) is None
    assert _extract_slippage({"validation": "not-a-dict"}) is None


def test_extract_quantity_variants():
    assert _extract_quantity({"analysis_metadata": {"quantity": 25}}) == 25.0
    assert _extract_quantity({"analysis_metadata": {"quantity": "bad"}}) is None
    assert _extract_quantity({"analysis_metadata": "bad"}) is None
    assert _extract_quantity({}) is None


def test_is_fill_confirmed_variants():
    assert _is_fill_confirmed({}) is True
    assert _is_fill_confirmed({"unconfirmedFill": True}) is False
    assert _is_fill_confirmed({"analysis_metadata": {"unconfirmed_fill": True}}) is False
    assert _is_fill_confirmed({"exitPriceSource": "signal_fallback"}) is False
    assert _is_fill_confirmed({"exitPriceSource": "signal_price"}) is False
    assert _is_fill_confirmed({"exitPriceSource": "ibkr_execution"}) is True


def test_first_present_and_format_and_compare():
    assert _first_present({"validation": {"a": 1}}, ("validation",)) == {"a": 1}
    assert _first_present({"validation": "x"}, ("validation",)) is None
    assert _first_present({}, ("validation",)) is None
    assert _format_value(None) == "N/A"
    assert _format_value(1.234, "%", 1) == "1.2%"
    assert _compare_status(None, 1.0) == "UNMEASURED"
    assert _compare_status(2.0, 1.0, higher_is_better=True) == "BEATING"
    assert _compare_status(0.5, 1.0, higher_is_better=True) == "LAGGING"
    assert _compare_status(0.5, 1.0, higher_is_better=False) == "BEATING"


# ---------------------------------------------------------------------------
# Core engine behaviour
# ---------------------------------------------------------------------------

def test_empty_trades_returns_na_not_fabricated_grade():
    res = calculate_engine_performance(
        trades=[], bot_overview_data={}, bot_configs=[], period="YTD",
        bot_scope="ALL", mode="both", now=FIXED_NOW,
    )
    assert isinstance(res, EnginePerformanceResponse)
    assert res.scorecard.overall_grade == "N/A"
    assert res.scorecard.overall_score is None
    assert res.scorecard.sample_confidence == "INSUFFICIENT"
    assert res.executive_kpis.total_trades == 0
    assert res.executive_kpis.annualized_sharpe is None
    assert res.executive_kpis.max_drawdown_pct is None
    assert res.executive_kpis.fill_confirmation_rate_pct is None
    assert res.executive_kpis.avg_slippage_per_share is None
    assert res.executive_kpis.nav == 0.0
    assert res.bots == []
    assert any(d.level == "INFO" for d in res.diagnostics)


def test_small_sample_is_not_graded(sample_trades, sample_bot_configs):
    res = calculate_engine_performance(
        trades=sample_trades, bot_overview_data={}, bot_configs=sample_bot_configs,
        period="YTD", bot_scope="ALL", mode="all", now=FIXED_NOW,
    )
    assert res.scorecard.overall_grade == "N/A"
    assert res.scorecard.sample_confidence == "INSUFFICIENT"
    assert res.executive_kpis.total_trades == 4
    # Each small-sample bot is flagged, not rated
    for b in res.bots:
        if b.trades_count < pe.MIN_TRADES_PER_BOT_FOR_GRADE:
            assert b.rating_grade == "N/A"
            assert b.rating_score is None
            assert b.status_badge == "LOW_SAMPLE"


def test_adequate_sample_is_graded():
    trades = _make_adequate_trades(n=45, days=30)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "strategy": "sma_crossover", "enabled": True, "allocated_capital": 60000.0}]
    spy = [100 + (i % 5) for i in range(60)]
    res = calculate_engine_performance(
        trades=trades, bot_overview_data={"NVDA": {"unrealizedPnl": 100.0}},
        bot_configs=cfg, period="1M", bot_scope="ALL", mode="live",
        benchmark_symbol="SPY", benchmark_price_series=spy, now=FIXED_NOW,
    )
    assert res.scorecard.overall_grade != "N/A"
    assert res.scorecard.overall_score is not None
    assert res.scorecard.sample_confidence in ("LOW", "ADEQUATE")
    assert res.executive_kpis.annualized_sharpe is not None
    assert res.executive_kpis.max_drawdown_pct is not None
    assert res.benchmarks.benchmark_source == "measured"
    assert res.executive_kpis.mdd_reduction_vs_benchmark_pct is not None
    bot = res.bots[0]
    assert bot.rating_grade != "N/A"
    assert bot.rating_score is not None
    assert bot.fill_confirmation_rate_pct == 100.0
    assert bot.slippage_sample_size == 45


def test_mode_filters_and_unknown_mode(sample_trades, sample_bot_configs):
    res_all = calculate_engine_performance(
        trades=sample_trades, bot_overview_data={}, bot_configs=sample_bot_configs,
        period="YTD", bot_scope="ALL", mode="all", now=FIXED_NOW,
    )
    assert res_all.executive_kpis.total_trades == 4

    res_paper = calculate_engine_performance(
        trades=sample_trades, bot_overview_data={}, bot_configs=sample_bot_configs,
        period="YTD", bot_scope="ALL", mode="paper", now=FIXED_NOW,
    )
    assert res_paper.executive_kpis.total_trades == 2
    assert res_paper.mode == "paper"

    res_live = calculate_engine_performance(
        trades=sample_trades, bot_overview_data={}, bot_configs=sample_bot_configs,
        period="YTD", bot_scope="ALL", mode="live", now=FIXED_NOW,
    )
    assert res_live.executive_kpis.total_trades == 2

    # Invalid mode falls back to all; "both" normalizes to all
    assert res_paper.mode == "paper"
    res_invalid = calculate_engine_performance(
        trades=sample_trades, bot_overview_data={}, bot_configs=sample_bot_configs,
        period="YTD", bot_scope="ALL", mode="bogus", now=FIXED_NOW,
    )
    assert res_invalid.mode == "all"


def test_trade_without_mode_excluded_from_mode_filters():
    trades = [
        {"timestamp": "2026-10-12T14:30:00Z", "symbol": "NVDA", "profitLoss": 100.0},
    ]
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 50000.0}]
    res_all = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                           period="1M", bot_scope="ALL", mode="all", now=FIXED_NOW)
    res_paper = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                             period="1M", bot_scope="ALL", mode="paper", now=FIXED_NOW)
    res_live = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                            period="1M", bot_scope="ALL", mode="live", now=FIXED_NOW)
    assert res_all.executive_kpis.total_trades == 1
    assert res_paper.executive_kpis.total_trades == 0
    assert res_live.executive_kpis.total_trades == 0


def test_only_configured_and_traded_symbols_are_listed():
    # IWM is configured but has no trades; XYZ has trades but no config.
    cfg = [{"name": "iwm_sma", "symbol": "IWM", "allocated_capital": 50000.0}]
    trades = [{"timestamp": "2026-10-12T14:30:00Z", "symbol": "XYZ", "profitLoss": 100.0,
               "trading_mode": "live"}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="all", now=FIXED_NOW)
    symbols = {b.symbol for b in res.bots}
    # No synthetic default symbols (NVDA/MSFT/CAT/QQQ) injected
    assert symbols == {"IWM", "XYZ"}
    xyz = next(b for b in res.bots if b.symbol == "XYZ")
    assert xyz.allocated_capital == 0.0


def test_benchmark_unavailable_when_no_series():
    trades = _make_adequate_trades(n=30, days=25)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="live",
                                       benchmark_price_series=None, now=FIXED_NOW)
    assert res.benchmarks.benchmark_source == "unavailable"
    assert res.executive_kpis.benchmark_mdd_pct is None
    assert res.executive_kpis.mdd_reduction_vs_benchmark_pct is None
    assert res.benchmarks.buy_and_hold[0].status == "UNMEASURED"


def test_benchmark_series_too_short_is_unavailable():
    trades = _make_adequate_trades(n=12, days=12)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="live",
                                       benchmark_price_series=[100.0], now=FIXED_NOW)
    assert res.benchmarks.benchmark_source == "unavailable"


def test_execution_integrity_warnings_and_bounds():
    # Unconfirmed fills + reconciliation mismatch -> WARNING diagnostics
    trades = _make_adequate_trades(n=30, days=25, unconfirmed_every=3, mismatched_every=4)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="live", now=FIXED_NOW)
    ids = {d.id for d in res.diagnostics}
    assert "diag-unconfirmed-fills" in ids
    assert "diag-reconciliation" in ids
    assert res.executive_kpis.fill_confirmation_rate_pct < 98.0
    assert res.executive_kpis.reconciliation_rate_pct < 99.0


def test_cost_drag_warning_and_measured_slippage():
    # 7 trades with heavy commissions/slippage -> friction warning + measured slippage
    trades = []
    for i in range(7):
        trades.append({
            "timestamp": (FIXED_NOW - timedelta(days=i)).isoformat(),
            "symbol": "NVDA", "profitLoss": 10.0, "trading_mode": "paper",
            "slippage": 0.50, "commission": 15.0,
            "analysis_metadata": {"quantity": 100},
        })
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 50000.0}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1W", bot_scope="ALL", mode="paper", now=FIXED_NOW)
    assert any("Friction Drag" in d.title for d in res.diagnostics)
    bot = next(b for b in res.bots if b.symbol == "NVDA")
    assert bot.cost_drag_pct is not None and bot.cost_drag_pct > 25.0
    assert bot.avg_slippage_per_share == 0.5


def test_scoped_bot_aggregation_and_summary():
    trades = _make_adequate_trades(symbol="NVDA", n=25, days=22)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "strategy": "sma_crossover",
            "allocated_capital": 60000.0}]
    res = calculate_engine_performance(
        trades=trades, bot_overview_data={"NVDA": {"unrealizedPnl": 400.0, "realizedPnl": 5000.0}},
        bot_configs=cfg, period="1M", bot_scope="NVDA", mode="live", now=FIXED_NOW,
    )
    assert "NVDA" in res.scorecard.summary_assessment
    assert res.scorecard.overall_score is not None


def test_scoped_bot_isolated_from_other_symbols():
    nvda = _make_adequate_trades(symbol="NVDA", n=20, days=20)
    iwm = [
        {
            "timestamp": (FIXED_NOW - timedelta(days=i)).isoformat(),
            "symbol": "IWM", "profitLoss": -400.0, "trading_mode": "live",
            "commission": 1.0, "analysis_metadata": {"quantity": 10},
        }
        for i in range(20)
    ]
    cfg = [
        {"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0},
        {"name": "iwm_sma", "symbol": "IWM", "allocated_capital": 60000.0},
    ]
    res_all = calculate_engine_performance(
        trades=nvda + iwm, bot_overview_data={}, bot_configs=cfg, period="1M",
        bot_scope="ALL", mode="live",
        account_overview_data={"net_liquidation_value": 999999.0}, now=FIXED_NOW)
    res_nvda = calculate_engine_performance(
        trades=nvda + iwm, bot_overview_data={}, bot_configs=cfg, period="1M",
        bot_scope="NVDA", mode="live",
        account_overview_data={"net_liquidation_value": 999999.0}, now=FIXED_NOW)

    # Portfolio view uses account NAV; single-bot view must not.
    assert res_all.executive_kpis.nav == 999999.0
    assert res_nvda.executive_kpis.nav != 999999.0
    # Scoped metrics reflect only NVDA, not the IWM losers.
    assert res_nvda.executive_kpis.total_trades == 20
    assert res_nvda.executive_kpis.total_net_pnl > res_all.executive_kpis.total_net_pnl
    assert res_nvda.executive_kpis.max_drawdown_pct != res_all.executive_kpis.max_drawdown_pct


def test_scope_resolves_by_bot_id_not_just_symbol():
    # The scope selector may carry the bot id (config "name") rather than the
    # ticker; resolution must handle both, case-insensitively.
    trades = _make_adequate_trades(symbol="NVDA", n=20, days=20)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res = calculate_engine_performance(
        trades=trades, bot_overview_data={}, bot_configs=cfg, period="1M",
        bot_scope="nvda_sma", mode="live", now=FIXED_NOW,
    )
    assert res.bot_scope == "NVDA"
    assert res.executive_kpis.total_trades == 20


def test_unresolved_scope_falls_back_to_all_and_warns():
    # A bot that has been removed/renamed must not be silently blended with the
    # rest of the portfolio; the fallback is explicit and flagged.
    nvda = _make_adequate_trades(symbol="NVDA", n=20, days=20)
    iwm = _make_adequate_trades(symbol="IWM", n=20, days=20)
    cfg = [
        {"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0},
        {"name": "iwm_sma", "symbol": "IWM", "allocated_capital": 60000.0},
    ]
    res = calculate_engine_performance(
        trades=nvda + iwm, bot_overview_data={}, bot_configs=cfg, period="1M",
        bot_scope="REMOVED_BOT", mode="live", now=FIXED_NOW,
    )
    # Scope collapses to ALL, and both bots' trades are aggregated.
    assert res.bot_scope == "ALL"
    assert res.executive_kpis.total_trades == 40
    diag = next(d for d in res.diagnostics if d.id == "diag-unresolved-scope")
    assert diag.level == "WARNING"
    assert "REMOVED_BOT" in diag.detail


def test_removed_bot_with_history_is_listed_and_labelled():
    # XYZ traded historically but is no longer configured -> kept for the record,
    # labelled REMOVED rather than implied to be ACTIVE.
    cfg = [{"name": "iwm_sma", "symbol": "IWM", "allocated_capital": 50000.0}]
    trades = [{"timestamp": "2026-10-12T14:30:00Z", "symbol": "XYZ", "profitLoss": 100.0,
               "trading_mode": "live"}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="all", now=FIXED_NOW)
    xyz = next(b for b in res.bots if b.symbol == "XYZ")
    assert xyz.status == "REMOVED"
    iwm = next(b for b in res.bots if b.symbol == "IWM")
    assert iwm.status == "ACTIVE"


def test_account_overview_nav_valid():
    trades = _make_adequate_trades(n=12, days=12)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res_valid = calculate_engine_performance(
        trades=trades, bot_overview_data={}, bot_configs=cfg, period="1M",
        account_overview_data={"net_liquidation_value": 123456.0}, now=FIXED_NOW)
    assert res_valid.executive_kpis.nav == 123456.0


def test_account_overview_nav_invalid_value_falls_back():
    trades = _make_adequate_trades(n=12, days=12)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res_invalid = calculate_engine_performance(
        trades=trades, bot_overview_data={}, bot_configs=cfg, period="1M",
        account_overview_data={"net_liquidation_value": "not-a-float"}, now=FIXED_NOW)
    # Falls back to derived NAV (allocation + net pnl), never crashes
    assert res_invalid.executive_kpis.nav == round(60000.0 + res_invalid.executive_kpis.total_net_pnl, 2)


def test_symbolless_trade_and_bad_allocation_are_ignored():
    trades = [
        # No symbol and no botName -> skipped from bucketing
        {"timestamp": "2026-10-12T14:30:00Z", "profitLoss": 100.0, "trading_mode": "live"},
        {"timestamp": "2026-10-12T15:30:00Z", "symbol": "NVDA", "profitLoss": 200.0, "trading_mode": "live"},
    ]
    # Non-numeric allocated_capital -> guarded to 0.0
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": "bad-value"}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="all", now=FIXED_NOW)
    nvda = next(b for b in res.bots if b.symbol == "NVDA")
    assert nvda.allocated_capital == 0.0
    assert res.executive_kpis.total_trades == 1


def test_dict_configs_and_old_trade_excluded():
    trades = [
        {"timestamp": "2026-10-12T14:30:00Z", "symbol": "NVDA", "profitLoss": 100.0, "trading_mode": "live"},
        {"timestamp": "2020-01-01T00:00:00Z", "symbol": "NVDA", "profitLoss": 999.0, "trading_mode": "live"},
    ]
    configs = {"a": {"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 50000.0}}
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=configs,
                                       period="1M", bot_scope="ALL", mode="all", now=FIXED_NOW)
    # Only the in-period trade counts
    assert res.executive_kpis.total_trades == 1


def test_capital_underperformance_warning_and_idle_bots():
    # Engine drawdown deeper than benchmark drawdown
    trades = []
    for i in range(12):
        pnl = -500.0 if i % 2 else 50.0
        trades.append({"timestamp": (FIXED_NOW - timedelta(days=i)).isoformat(),
                       "symbol": "NVDA", "profitLoss": pnl, "trading_mode": "live",
                       "commission": 1.0, "analysis_metadata": {"quantity": 10}})
    cfg = [
        {"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 50000.0},
        {"name": "iwm_sma", "symbol": "IWM", "allocated_capital": 50000.0},  # idle
    ]
    # Benchmark has only a shallow dip, so the engine's deeper drawdown is worse
    spy = [100.0, 99.0, 98.0, 99.0, 100.0, 101.0, 100.5, 101.5]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="all",
                                       benchmark_symbol="SPY", benchmark_price_series=spy, now=FIXED_NOW)
    ids = {d.id for d in res.diagnostics}
    assert "diag-cap-underperform" in ids
    assert "diag-idle-bots" in ids


def test_capital_preservation_success_diagnostic():
    # Engine nearly flat while benchmark sells off hard -> big DD reduction
    trades = []
    for i in range(14):
        trades.append({"timestamp": (FIXED_NOW - timedelta(days=i)).isoformat(),
                       "symbol": "NVDA", "profitLoss": 5.0, "trading_mode": "live",
                       "commission": 1.0, "analysis_metadata": {"quantity": 10}})
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 50000.0}]
    spy = [200 - 3 * i for i in range(40)]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="all",
                                       benchmark_symbol="SPY", benchmark_price_series=spy, now=FIXED_NOW)
    assert any(d.id == "diag-cap-preservation" for d in res.diagnostics)


def test_cache_returns_deep_copy_and_marks_cached():
    trades = _make_adequate_trades(n=30, days=25)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    first = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                         period="1M", bot_scope="ALL", mode="live", now=FIXED_NOW)
    assert first.cached is False
    second = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                          period="1M", bot_scope="ALL", mode="live", now=FIXED_NOW)
    assert second.cached is True
    # Mutating the cache hit must not mutate the original object
    assert first.cached is False


def test_ratio_suppression_below_min_observations():
    # 12 trades but spread over only 12 days -> daily obs < MIN_OBSERVATIONS_FOR_RATIOS
    trades = _make_adequate_trades(n=12, days=12)
    cfg = [{"name": "nvda_sma", "symbol": "NVDA", "allocated_capital": 60000.0}]
    res = calculate_engine_performance(trades=trades, bot_overview_data={}, bot_configs=cfg,
                                       period="1M", bot_scope="ALL", mode="live", now=FIXED_NOW)
    assert res.executive_kpis.annualized_sharpe is None
    assert res.executive_kpis.sortino_ratio is None


# ---------------------------------------------------------------------------
# API route integration tests
# ---------------------------------------------------------------------------

def test_get_performance_html_page(test_client):
    response = test_client.get("/performance")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "Trading Engine Performance" in html
    assert 'id="scorecardGrade"' in html
    assert 'id="scorecardNumeric"' in html
    assert 'id="kpiCalmar"' in html
    assert 'id="benchmarkSelect"' in html
    assert 'id="tableSec605"' in html
    assert 'id="tableInstCta"' in html
    assert 'id="benchmarkRadarChart"' in html
    assert 'id="botsTableBody"' in html
    assert 'id="diagnosticsGrid"' in html
    assert 'href="/performance"' in html


def test_get_engine_performance_api(test_client, sample_bot_configs):
    async def fake_history(days=60, response=None):
        return {"trades": _make_adequate_trades(n=14, days=12)}

    mock_db = MagicMock()
    acct_doc = mock_db.collection.return_value.document.return_value.get.return_value
    acct_doc.exists = True
    acct_doc.to_dict.return_value = {"net_liquidation_value": 250000.0}

    with patch("main.get_all_trade_history", new=fake_history), \
         patch("main.load_bot_configs", return_value={"bots": sample_bot_configs}), \
         patch("main._fetch_benchmark_price_series", return_value=None), \
         patch.object(main.dashboard_data, "db", mock_db), \
         patch.object(main.dashboard_data, "_load_bot_data", return_value={}):
        response = test_client.get("/api/engine-performance?period=YTD&mode=all&bot=ALL&benchmark=SPY")
    assert response.status_code == 200
    data = response.json()
    assert "scorecard" in data
    assert "overall_grade" in data["scorecard"]
    assert "sample_confidence" in data["scorecard"]
    assert "executive_kpis" in data
    assert "metrics_basis" in data["executive_kpis"]
    assert data["executive_kpis"]["nav"] == 250000.0
    assert "benchmarks" in data
    assert "benchmark_source" in data["benchmarks"]
    assert "sec_rule_605" in data["benchmarks"]
    assert "institutional_cta" in data["benchmarks"]
    assert "buy_and_hold" in data["benchmarks"]
    assert "bots" in data
    assert "diagnostics" in data


def test_get_engine_performance_api_with_benchmark_series(test_client, sample_bot_configs):
    async def fake_history(days=60, response=None):
        return {"trades": _make_adequate_trades(n=14, days=12)}

    with patch("main.get_all_trade_history", new=fake_history), \
         patch("main.load_bot_configs", return_value={"bots": sample_bot_configs}), \
         patch("main._fetch_benchmark_price_series", return_value=[100.0, 101.0, 99.0, 102.0]), \
         patch.object(main.dashboard_data, "_load_bot_data", return_value={}):
        response = test_client.get("/api/engine-performance?benchmark=QQQ")
    assert response.status_code == 200
    assert response.json()["benchmarks"]["benchmark_source"] == "measured"


def test_get_engine_performance_api_error_handling(test_client):
    with patch("main.calculate_engine_performance", side_effect=ValueError("Engine calculation error")):
        response = test_client.get("/api/engine-performance")
        assert response.status_code == 500
        assert "Failed to generate engine performance" in response.json()["detail"]


def test_data_freshness_fields_reflect_evaluated_trades():
    trades = _make_adequate_trades(n=8, days=6)
    res = calculate_engine_performance(
        trades=trades, bot_overview_data={}, bot_configs=[], period="1M",
        bot_scope="ALL", mode="all", now=FIXED_NOW,
    )
    assert res.data_as_of is not None
    assert res.data_oldest is not None
    # Newest evaluated trade is the last one generated.
    expected_as_of = max(datetime.fromisoformat(t["timestamp"]) for t in trades)
    assert datetime.fromisoformat(res.data_as_of) == expected_as_of


def test_empty_period_reports_no_freshness():
    res = calculate_engine_performance(
        trades=[], bot_overview_data={}, bot_configs=[], period="1M",
        bot_scope="ALL", mode="all", now=FIXED_NOW,
    )
    assert res.data_as_of is None
    assert res.data_oldest is None
    assert res.history_truncated is False


def test_history_truncated_flag():
    trades = _make_adequate_trades(n=10, days=10)
    res = calculate_engine_performance(
        trades=trades, bot_overview_data={}, bot_configs=[], period="1M",
        bot_scope="ALL", mode="all", history_limit=10, now=FIXED_NOW,
    )
    assert res.history_truncated is True

    # Different scope => different cache key, so this is a genuine recompute.
    res2 = calculate_engine_performance(
        trades=trades, bot_overview_data={}, bot_configs=[], period="1M",
        bot_scope="ALL", mode="live", history_limit=100, now=FIXED_NOW,
    )
    assert res2.history_truncated is False


def test_clear_performance_cache_drops_entries():
    with pe._perf_cache_lock:
        pe._perf_cache["sentinel"] = {"data": None, "timestamp": datetime.now()}
    pe.clear_performance_cache()
    with pe._perf_cache_lock:
        assert pe._perf_cache == {}


def test_trade_history_cache_is_keyed_by_days(test_client):
    # A cached 365-day window must never be served to a 3650-day request.
    with main._trade_history_cache['lock']:
        main._trade_history_cache['data'][365] = {"trades": [{"symbol": "OLD365"}], "days": 365, "count": 1}
        main._trade_history_cache['timestamp'][365] = datetime.now()

    with patch.object(main.dashboard_data.trades_ref, "where") as mock_w:
        mock_w.return_value.order_by.return_value.limit.return_value.stream.return_value = []
        res = test_client.get("/api/trade-history/all?days=3650")

    assert res.status_code == 200
    body = res.json()
    assert body["days"] == 3650
    assert body["trades"] == []  # did NOT receive the 365-day payload
    with main._trade_history_cache['lock']:
        assert 365 in main._trade_history_cache['data']  # other window preserved


def test_performance_window_uses_longer_cache_ttl(test_client):
    with patch.object(main.dashboard_data.trades_ref, "where") as mock_w:
        mock_w.return_value.order_by.return_value.limit.return_value.stream.return_value = []
        res = test_client.get(f"/api/trade-history/all?days={main.PERFORMANCE_HISTORY_LOOKBACK_DAYS}")
    assert res.status_code == 200
    with main._trade_history_cache['lock']:
        assert main._trade_history_cache['ttl'][main.PERFORMANCE_HISTORY_LOOKBACK_DAYS] == main.PERFORMANCE_TRADE_CACHE_TTL_SECONDS


def _mock_account_db():
    mock_db = MagicMock()
    acct_doc = mock_db.collection.return_value.document.return_value.get.return_value
    acct_doc.exists = True
    acct_doc.to_dict.return_value = {"net_liquidation_value": 100000.0}
    return mock_db


def test_force_refresh_bypasses_caches(test_client, sample_bot_configs):
    async def fake_history(days=60, response=None):
        return {"trades": _make_adequate_trades(n=12, days=10)}

    # Seed caches with sentinels that force=1 must drop.
    with main._trade_history_cache['lock']:
        main._trade_history_cache['data'][main.PERFORMANCE_HISTORY_LOOKBACK_DAYS] = {"trades": [], "days": 3650, "count": 0}
        main._trade_history_cache['timestamp'][main.PERFORMANCE_HISTORY_LOOKBACK_DAYS] = datetime.now()
    with main._benchmark_series_lock:
        main._benchmark_series_cache["SPY_YTD"] = {"data": [1.0, 2.0], "timestamp": datetime.now()}

    with patch("main.get_all_trade_history", new=fake_history), \
         patch("main.load_bot_configs", return_value={"bots": sample_bot_configs}), \
         patch("main._fetch_benchmark_price_series", return_value=None), \
         patch.object(main.dashboard_data, "db", _mock_account_db()), \
         patch.object(main.dashboard_data, "_load_bot_data", return_value={}):
        response = test_client.get("/api/engine-performance?force=1")

    assert response.status_code == 200
    with main._trade_history_cache['lock']:
        assert main.PERFORMANCE_HISTORY_LOOKBACK_DAYS not in main._trade_history_cache['data']
    with main._benchmark_series_lock:
        assert main._benchmark_series_cache == {}


def test_get_performance_html_page_not_found(test_client):
    with patch("main.os.path.exists", return_value=False):
        response = test_client.get("/performance")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# main.py helper tests (period mapping + benchmark fetching)
# ---------------------------------------------------------------------------

def test_period_lookback_days():
    assert main._period_lookback_days("1D") == 2
    assert main._period_lookback_days("1W") == 8
    assert main._period_lookback_days("1M") == 31
    assert main._period_lookback_days("QTD") == 95
    assert main._period_lookback_days("YTD") == 370
    assert main._period_lookback_days("ALL") == main.PERFORMANCE_HISTORY_LOOKBACK_DAYS
    assert main._period_lookback_days("unknown") == 370


class _FakeSeries:
    def __init__(self, closes):
        self._closes = closes

    def tolist(self):
        return self._closes


class _FakeHist:
    def __init__(self, closes, empty=False):
        self._closes = closes
        self.empty = empty

    def __getitem__(self, key):
        return _FakeSeries(self._closes)


def test_fetch_benchmark_price_series_success_and_cache():
    hist = _FakeHist([100.0, 101.5, 102.0])
    ticker = MagicMock()
    ticker.history.return_value = hist
    with patch("yfinance.Ticker", return_value=ticker):
        series = main._fetch_benchmark_price_series("SPY", "1M", now=FIXED_NOW)
        assert series == [100.0, 101.5, 102.0]
        # Second call within TTL is served from cache (no extra provider call)
        series2 = main._fetch_benchmark_price_series("SPY", "1M", now=FIXED_NOW)
        assert series2 == [100.0, 101.5, 102.0]
        assert ticker.history.call_count == 1


def test_fetch_benchmark_price_series_filters_nan_and_empty():
    hist = _FakeHist([100.0, float("nan"), 0.0, 103.0])
    ticker = MagicMock()
    ticker.history.return_value = hist
    with patch("yfinance.Ticker", return_value=ticker):
        series = main._fetch_benchmark_price_series("QQQ", "1M", now=FIXED_NOW)
        assert series == [100.0, 103.0]

    empty_ticker = MagicMock()
    empty_ticker.history.return_value = _FakeHist([], empty=True)
    with patch("yfinance.Ticker", return_value=empty_ticker):
        assert main._fetch_benchmark_price_series("QQQ", "1Y", now=FIXED_NOW) is None


def test_fetch_benchmark_price_series_too_few_or_error():
    ticker = MagicMock()
    ticker.history.return_value = _FakeHist([100.0])  # fewer than 2 closes
    with patch("yfinance.Ticker", return_value=ticker):
        assert main._fetch_benchmark_price_series("IWM", "1W", now=FIXED_NOW) is None

    failing = MagicMock()
    failing.history.side_effect = RuntimeError("network down")
    with patch("yfinance.Ticker", return_value=failing):
        assert main._fetch_benchmark_price_series("DIA", "1M", now=FIXED_NOW) is None
