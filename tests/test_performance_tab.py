"""
Tests for Performance Dashboard Tab and Performance Engine
Validates:
- Services: performance_engine scoring, metrics aggregation, public benchmarks, diagnostics
- API endpoints: GET /performance (HTML), GET /api/engine-performance (JSON)
- Edge cases: empty trades, different periods, paper/live mode filters, caching, and error handling
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.performance_engine import (
    compute_grade_from_score,
    score_execution_quality,
    score_risk_adjusted_returns,
    score_expectancy_and_edge,
    score_capital_preservation,
    calculate_engine_performance,
    EnginePerformanceResponse,
)
from main import app, authenticated_sessions


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
            "analysis_metadata": {"quantity": 50}
        },
        {
            "timestamp": "2026-10-11T15:00:00Z",
            "symbol": "IWM",
            "botId": 3,
            "profitLoss": -450.0,
            "trading_mode": "paper",
            "slippage": 0.02,
            "commission": 2.00,
            "analysis_metadata": {"quantity": 100}
        },
        {
            "timestamp": "2026-10-10T16:00:00Z",
            "symbol": "MSFT",
            "botId": 5,
            "profitLoss": 800.0,
            "trading_mode": "live",
            "validation": {"slippage": 0.01, "ibkr_commission": 1.50},
            "analysis_metadata": {"quantity": 30}
        },
        {
            "timestamp": "2026-10-09T17:00:00Z",
            "symbol": "CAT",
            "botId": 6,
            "profitLoss": 350.0,
            "trading_mode": "paper"
        }
    ]


@pytest.fixture
def sample_bot_configs():
    return [
        {"name": "nvda_sma", "symbol": "NVDA", "client_id": 4, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 60000.0},
        {"name": "iwm_sma", "symbol": "IWM", "client_id": 3, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 50000.0},
        {"name": "msft_sma", "symbol": "MSFT", "client_id": 5, "strategy": "sma_crossover", "enabled": True, "allocated_capital": 60000.0},
        {"name": "cat_sma", "symbol": "CAT", "client_id": 6, "strategy": "sma_crossover", "enabled": False, "allocated_capital": 40000.0},
    ]


# ---------------------------------------------------------------------------
# Unit Tests for Scoring Functions
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


def test_score_execution_quality():
    high_score = score_execution_quality(fill_rate_pct=99.0, rejection_rate_pct=0.0, eq_ratio_pct=84.0, cost_drag_pct=8.0)
    assert high_score >= 90

    mid_score = score_execution_quality(fill_rate_pct=96.0, rejection_rate_pct=0.2, eq_ratio_pct=92.0, cost_drag_pct=15.0)
    assert 70 <= mid_score < 95

    low_score = score_execution_quality(fill_rate_pct=85.0, rejection_rate_pct=2.0, eq_ratio_pct=125.0, cost_drag_pct=35.0)
    assert low_score < 60


def test_score_risk_adjusted_returns():
    high = score_risk_adjusted_returns(sharpe=2.2, sortino=2.8, calmar=2.5)
    assert high >= 90

    mid = score_risk_adjusted_returns(sharpe=1.2, sortino=1.5, calmar=1.2)
    assert 70 <= mid < 85

    low = score_risk_adjusted_returns(sharpe=0.2, sortino=0.3, calmar=0.1)
    assert low < 50

    none_handled = score_risk_adjusted_returns(sharpe=None, sortino=None, calmar=None)
    assert 50 <= none_handled <= 80


def test_score_expectancy_and_edge():
    high = score_expectancy_and_edge(profit_factor=2.2, win_rate_pct=65.0, payoff_ratio=1.5, expectancy_dollars=60.0)
    assert high >= 90

    mid = score_expectancy_and_edge(profit_factor=1.4, win_rate_pct=50.0, payoff_ratio=1.1, expectancy_dollars=10.0)
    assert 60 <= mid < 85

    low = score_expectancy_and_edge(profit_factor=0.8, win_rate_pct=35.0, payoff_ratio=0.7, expectancy_dollars=-25.0)
    assert low < 50


def test_score_capital_preservation():
    high = score_capital_preservation(max_drawdown_pct=-3.0, mdd_reduction_vs_benchmark_pct=65.0)
    assert high >= 90

    mid = score_capital_preservation(max_drawdown_pct=-11.0, mdd_reduction_vs_benchmark_pct=25.0)
    assert 65 <= mid < 85

    low = score_capital_preservation(max_drawdown_pct=-26.0, mdd_reduction_vs_benchmark_pct=-10.0)
    assert low < 45


# ---------------------------------------------------------------------------
# Core Performance Engine Tests
# ---------------------------------------------------------------------------

def test_calculate_engine_performance_all_modes(sample_trades, sample_bot_configs):
    fixed_now = datetime(2026, 10, 13, 18, 0, 0, tzinfo=timezone.utc)
    bot_overview = {
        "NVDA": {"unrealizedPnl": 300.0, "realizedPnl": 1250.0},
        "IWM": {"unrealizedPnl": -50.0, "realizedPnl": -450.0}
    }
    acct_overview = {"net_liquidation_value": 260000.0}

    # 1. Period YTD in all mode
    res = calculate_engine_performance(
        trades=sample_trades,
        bot_overview_data=bot_overview,
        bot_configs=sample_bot_configs,
        account_overview_data=acct_overview,
        period="YTD",
        bot_scope="ALL",
        mode="all",
        benchmark_symbol="SPY",
        now=fixed_now
    )

    assert isinstance(res, EnginePerformanceResponse)
    assert res.scorecard.overall_grade in ("A+", "A", "A-", "B+", "B", "B-", "C+", "C")
    assert 0 <= res.scorecard.overall_score <= 100
    assert res.executive_kpis.total_trades == 4
    assert res.executive_kpis.win_rate_pct > 0.0
    assert len(res.bots) >= 4
    assert len(res.benchmarks.sec_rule_605) >= 3
    assert len(res.benchmarks.institutional_cta) >= 4
    assert len(res.benchmarks.buy_and_hold) >= 2
    assert len(res.diagnostics) >= 1

    # 2. Paper mode filter
    res_paper = calculate_engine_performance(
        trades=sample_trades,
        bot_overview_data=bot_overview,
        bot_configs=sample_bot_configs,
        account_overview_data=acct_overview,
        period="1M",
        bot_scope="ALL",
        mode="paper",
        now=fixed_now
    )
    assert res_paper.mode == "paper"
    assert res_paper.executive_kpis.total_trades == 2  # Only IWM & CAT in paper

    # 3. Live mode filter
    res_live = calculate_engine_performance(
        trades=sample_trades,
        bot_overview_data=bot_overview,
        bot_configs=sample_bot_configs,
        account_overview_data=acct_overview,
        period="1M",
        bot_scope="ALL",
        mode="live",
        now=fixed_now
    )
    assert res_live.mode == "live"
    assert res_live.executive_kpis.total_trades == 2  # Only NVDA & MSFT in live

    # 4. Cache hit test
    res_cached = calculate_engine_performance(
        trades=sample_trades,
        bot_overview_data=bot_overview,
        bot_configs=sample_bot_configs,
        period="YTD",
        bot_scope="ALL",
        mode="all",
        benchmark_symbol="SPY",
        now=fixed_now
    )
    assert res_cached.cached is True


def test_calculate_engine_performance_empty_trades():
    fixed_now = datetime(2026, 10, 13, 18, 0, 0, tzinfo=timezone.utc)
    res = calculate_engine_performance(
        trades=[],
        bot_overview_data={},
        bot_configs=[],
        account_overview_data=None,
        period="1D",
        bot_scope="ALL",
        mode="both",
        now=fixed_now
    )
    assert res.executive_kpis.total_trades == 0
    assert res.executive_kpis.win_rate_pct == 0.0
    assert res.scorecard.overall_grade is not None


# ---------------------------------------------------------------------------
# API Route Integration Tests
# ---------------------------------------------------------------------------

def test_get_performance_html_page(test_client):
    response = test_client.get("/performance")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Core elements from design
    assert "Trading Engine Performance" in html
    assert 'id="scorecardGrade"' in html
    assert 'id="scorecardNumeric"' in html
    assert 'id="tableSec605"' in html
    assert 'id="tableInstCta"' in html
    assert 'id="benchmarkRadarChart"' in html
    assert 'id="botsTableBody"' in html
    assert 'id="diagnosticsGrid"' in html
    assert 'href="/performance"' in html


def test_get_engine_performance_api(test_client):
    response = test_client.get("/api/engine-performance?period=YTD&mode=all&bot=ALL&benchmark=SPY")
    assert response.status_code == 200
    data = response.json()

    assert "scorecard" in data
    assert "overall_grade" in data["scorecard"]
    assert "overall_score" in data["scorecard"]
    assert "executive_kpis" in data
    assert "benchmarks" in data
    assert "sec_rule_605" in data["benchmarks"]
    assert "institutional_cta" in data["benchmarks"]
    assert "buy_and_hold" in data["benchmarks"]
    assert "bots" in data
    assert "diagnostics" in data


def test_get_engine_performance_api_error_handling(test_client):
    with patch("main.calculate_engine_performance", side_effect=ValueError("Engine calculation error")):
        response = test_client.get("/api/engine-performance")
        assert response.status_code == 500
        assert "Failed to generate engine performance" in response.json()["detail"]


def test_scoring_boundaries_coverage():
    # Execution boundaries
    s1 = score_execution_quality(91.0, 0.8, 102.0, 22.0)
    assert 60 <= s1 <= 85

    # Risk-adjusted boundaries
    s2 = score_risk_adjusted_returns(sharpe=1.6, sortino=1.9, calmar=1.6)
    assert 85 <= s2 <= 95
    s3 = score_risk_adjusted_returns(sharpe=0.7, sortino=0.9, calmar=0.6)
    assert 55 <= s3 <= 70

    # Expectancy boundaries
    s4 = score_expectancy_and_edge(profit_factor=1.8, win_rate_pct=62.0, payoff_ratio=2.0, expectancy_dollars=-4.0)
    assert 70 <= s4 <= 90
    s5 = score_expectancy_and_edge(profit_factor=1.1, win_rate_pct=42.0, payoff_ratio=1.0, expectancy_dollars=0.0)
    assert 40 <= s5 <= 65

    # Capital preservation boundaries
    s6 = score_capital_preservation(max_drawdown_pct=-7.0, mdd_reduction_vs_benchmark_pct=35.0)
    assert 80 <= s6 <= 95
    s7 = score_capital_preservation(max_drawdown_pct=-14.0, mdd_reduction_vs_benchmark_pct=15.0)
    assert 65 <= s7 <= 80
    s8 = score_capital_preservation(max_drawdown_pct=-20.0, mdd_reduction_vs_benchmark_pct=5.0)
    assert 50 <= s8 <= 65


def test_calculate_engine_performance_high_friction_and_periods(sample_bot_configs):
    fixed_now = datetime(2026, 10, 13, 18, 0, 0, tzinfo=timezone.utc)
    # 7 trades for NVDA with high commissions to trigger high cost drag warning
    high_friction_trades = [
        {
            "timestamp": "2026-10-12T14:30:00Z",
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": 10.0,
            "trading_mode": "paper",
            "slippage": 0.50,
            "commission": 15.0,
            "analysis_metadata": {"quantity": 100}
        }
        for _ in range(7)
    ]
    res = calculate_engine_performance(
        trades=high_friction_trades,
        bot_overview_data={"NVDA": {"unrealizedPnl": 0.0, "realizedPnl": 70.0}},
        bot_configs=sample_bot_configs,
        account_overview_data={"net_liquidation_value": 200000.0},
        period="1W",
        bot_scope="ALL",
        mode="paper",
        benchmark_symbol="QQQ",
        now=fixed_now
    )
    diag_titles = [d.title for d in res.diagnostics]
    assert any("Friction Drag" in t for t in diag_titles)


def test_get_performance_html_page_not_found(test_client):
    with patch("main.os.path.join", return_value="/non/existent/performance.html"):
        response = test_client.get("/performance")
        assert response.status_code == 404


def test_performance_engine_scoped_bot_and_edge_branches(sample_bot_configs):
    fixed_now = datetime(2026, 10, 13, 18, 0, 0, tzinfo=timezone.utc)
    
    # 1. Test trend follower profile edge score branch
    edge_score = score_expectancy_and_edge(
        profit_factor=2.0,
        win_rate_pct=50.0,
        payoff_ratio=2.5,
        expectancy_dollars=45.0
    )
    assert edge_score >= 80

    # 2. Trades including custom symbol, invalid quantity, invalid slippage, and optimal bot metrics
    trades = [
        {
            "timestamp": "2026-10-12T14:30:00Z",
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": 2500.0,
            "trading_mode": "live",
            "slippage": "not-a-number",
            "commission": 1.50,
            "analysis_metadata": {"quantity": "invalid-qty"}
        },
        {
            "timestamp": "2026-10-11T14:30:00Z",
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": 1800.0,
            "trading_mode": "live",
            "slippage": 0.01,
            "commission": 1.50,
            "analysis_metadata": {"quantity": 100}
        },
        {
            "timestamp": "2026-10-10T14:30:00Z",
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": 1200.0,
            "trading_mode": "live",
            "slippage": 0.01,
            "commission": 1.50,
            "analysis_metadata": {"quantity": 100}
        },
        {
            "timestamp": "2026-10-09T14:30:00Z",
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": -200.0,
            "trading_mode": "live",
            "slippage": 0.01,
            "commission": 1.50,
            "analysis_metadata": {"quantity": 100}
        },
        {
            "timestamp": "2026-10-08T14:30:00Z",
            "symbol": "XYZ_CUSTOM",
            "botId": 99,
            "profitLoss": 500.0,
            "trading_mode": "live",
            "commission": 1.0
        },
        {
            "timestamp": "2025-01-01T00:00:00Z",  # older than 1W period start to hit line 409
            "symbol": "NVDA",
            "botId": 4,
            "profitLoss": 10.0,
            "trading_mode": "live"
        }
    ]

    # Scoped to NVDA (exercises scoped_bot branches lines 618-632 & 706-725, and optimal/healthy status)
    res_scoped = calculate_engine_performance(
        trades=trades,
        bot_overview_data={"NVDA": {"unrealizedPnl": 400.0, "realizedPnl": 5300.0}},
        bot_configs=[],  # exercises total_allocated fallback line 653
        account_overview_data={"net_liquidation_value": "not_a_float"},  # exercises non-float handling
        period="1W",
        bot_scope="NVDA",
        mode="invalid_mode_fallback",  # exercises invalid mode fallback to all
        benchmark_symbol="SPY",
        now=fixed_now
    )
    assert res_scoped.scorecard.overall_score > 0
    assert "NVDA" in res_scoped.scorecard.summary_assessment
    assert res_scoped.scorecard.overall_grade == "A+"

    # Scoped to healthy bot (with moderate returns to hit HEALTHY branch lines 572-573)
    healthy_trades = [
        {
            "timestamp": "2026-10-12T14:30:00Z",
            "symbol": "IWM",
            "botId": 3,
            "profitLoss": 15.0,
            "trading_mode": "paper",
            "commission": 1.0,
            "analysis_metadata": {"quantity": 10}
        },
        {
            "timestamp": "2026-10-11T14:30:00Z",
            "symbol": "IWM",
            "botId": 3,
            "profitLoss": -40.0,
            "trading_mode": "paper",
            "commission": 1.0,
            "analysis_metadata": {"quantity": 10}
        },
        {
            "timestamp": "2026-10-10T14:30:00Z",
            "symbol": "IWM",
            "botId": 3,
            "profitLoss": 10.0,
            "trading_mode": "paper",
            "commission": 1.0,
            "analysis_metadata": {"quantity": 10}
        },
        {
            "timestamp": "2026-10-09T14:30:00Z",
            "symbol": "IWM",
            "botId": 3,
            "profitLoss": -25.0,
            "trading_mode": "paper",
            "commission": 1.0,
            "analysis_metadata": {"quantity": 10}
        },
    ]
    res_healthy = calculate_engine_performance(
        trades=healthy_trades,
        bot_overview_data={"IWM": {"unrealizedPnl": 0.0, "realizedPnl": -40.0}},
        bot_configs=sample_bot_configs,
        period="1W",
        bot_scope="IWM",
        mode="paper",
        benchmark_symbol="SPY",
        now=fixed_now
    )
    assert res_healthy.scorecard.overall_score > 0


