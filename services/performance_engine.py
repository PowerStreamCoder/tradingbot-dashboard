"""
Trading Engine Performance & Benchmarking Engine

Provides:
- Multi-pillar evaluation of trading engine execution and quantitative quality
- Algorithmic rating & scorecard system (Letter grades A+ to D, 0-100 score)
- Measured execution-quality metrics derived from logged fills:
    * Fill confirmation rate (IBKR execution vs signal-price fallback)
    * P&L reconciliation rate (bot vs IBKR validation)
    * Reported slippage (only when explicitly recorded on the trade)
- Quantitative strategy metrics (Sharpe, Sortino, Calmar, Profit Factor,
  Expectancy, Max Drawdown) - guarded against small-sample annualization
- Public benchmark comparisons:
    * SEC Rule 605 / institutional execution integrity standards
    * Institutional Systematic Quant & CTA Standards (Barclay CTA, HFRX)
    * Buy-and-Hold baselines computed from a real price series when supplied
- Individual bot attribution, scorecards, and status ratings
- Automated diagnostic findings and prioritized improvement levers

Data integrity principles (v2.0.0):
- No fabricated metrics. Any value that cannot be measured is returned as
  ``None`` and flagged (status ``UNMEASURED`` / basis ``unavailable``) rather
  than substituted with a hard-coded "typical" constant.
- Grades are suppressed (``N/A``) until a minimum sample size is reached, and
  risk ratios are only annualized once enough daily observations exist.
"""

from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone
from threading import Lock
import math
import logging

from services.pnl_statement_engine import (
    parse_iso_datetime,
    get_period_start_date,
    estimate_trade_commission,
    calculate_profit_factor,
    calculate_sharpe_and_sortino,
    calculate_max_drawdown,
    DEFAULT_RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0
_perf_cache: Dict[str, Dict[str, Any]] = {}
_perf_cache_lock = Lock()


def clear_performance_cache() -> None:
    """Drop every cached performance response.

    Used by the manual-refresh path so an on-demand refresh recomputes from the
    latest Firestore data instead of replaying a result cached moments ago.
    """
    with _perf_cache_lock:
        _perf_cache.clear()

# ---------------------------------------------------------------------------
# Sample-size governance thresholds
# ---------------------------------------------------------------------------
# Minimum trades / distinct trading days before a letter grade is issued.
MIN_TRADES_FOR_GRADE = 10
MIN_DAYS_FOR_GRADE = 10
# Minimum sample before an individual bot is rated.
MIN_TRADES_PER_BOT_FOR_GRADE = 5
# Minimum distinct daily observations before annualizing Sharpe/Sortino/Calmar.
MIN_OBSERVATIONS_FOR_RATIOS = 20
# "Adequate" sample thresholds (upper confidence band).
ADEQUATE_TRADES = 30
ADEQUATE_DAYS = 20


# ---------------------------------------------------------------------------
# Pydantic Schemas for Performance API
# ---------------------------------------------------------------------------

class BenchmarkComparisonMetric(BaseModel):
    name: str
    category: str  # "EXECUTION", "RISK_ADJUSTED", "CAPITAL_PRESERVATION"
    engine_value: Optional[float] = None
    engine_formatted: str = "N/A"
    benchmark_name: str
    benchmark_value: Optional[float] = None
    benchmark_formatted: str = "N/A"
    status: str  # "BEATING", "MATCHING", "LAGGING", "UNMEASURED"
    data_basis: str = "static_reference"  # measured | static_reference | unavailable
    description: str


class PublicBenchmarkReport(BaseModel):
    sec_rule_605: List[BenchmarkComparisonMetric]
    institutional_cta: List[BenchmarkComparisonMetric]
    buy_and_hold: List[BenchmarkComparisonMetric]
    benchmark_source: str = "unavailable"  # measured | static_reference | unavailable


class BotPerformanceRecord(BaseModel):
    bot_id: str
    symbol: str
    strategy: str
    trading_mode: str
    status: str
    allocated_capital: float
    trades_count: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: Optional[float] = None
    payoff_ratio: Optional[float] = None
    expectancy_dollars: float = 0.0
    expectancy_pct: float = 0.0
    gross_pnl: float = 0.0
    commissions: float = 0.0
    net_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    roc_pct: float = 0.0
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    avg_slippage_per_share: Optional[float] = None
    slippage_sample_size: int = 0
    cost_drag_pct: Optional[float] = None
    fill_confirmation_rate_pct: Optional[float] = None
    reconciliation_rate_pct: Optional[float] = None
    rating_grade: str = "N/A"
    rating_score: Optional[int] = None
    status_badge: str = "LOW_SAMPLE"  # OPTIMAL, HEALTHY, NEEDS_TUNING, LOW_SAMPLE
    diagnostic_finding: str = "Insufficient data for a rating."


class EngineScorecard(BaseModel):
    overall_grade: str
    overall_score: Optional[int] = None
    execution_score: Optional[int] = None
    risk_adjusted_score: Optional[int] = None
    expectancy_score: Optional[int] = None
    capital_preservation_score: Optional[int] = None
    sample_confidence: str = "INSUFFICIENT"  # INSUFFICIENT, LOW, ADEQUATE
    summary_assessment: str


class EngineDiagnosticFinding(BaseModel):
    id: str
    level: str  # "SUCCESS", "INFO", "WARNING", "OPPORTUNITY"
    category: str  # "EXECUTION", "SIGNAL_FUNNEL", "RISK_PRESERVATION", "EXIT_CASCADE"
    title: str
    detail: str
    recommended_action: str
    guide_reference: str


class ExecutivePerformanceKPIs(BaseModel):
    nav: float
    total_net_pnl: float
    total_trades: int
    win_rate_pct: float
    profit_factor: Optional[float] = None
    payoff_ratio: Optional[float] = None
    expectancy_dollars: float = 0.0
    annualized_sharpe: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    avg_slippage_per_share: Optional[float] = None
    effective_over_quoted_ratio_pct: Optional[float] = None
    fill_rate_pct: Optional[float] = None
    rejection_rate_pct: Optional[float] = None
    fill_confirmation_rate_pct: Optional[float] = None
    reconciliation_rate_pct: Optional[float] = None
    transaction_cost_drag_pct: Optional[float] = None
    realized_volatility_pct: Optional[float] = None
    benchmark_mdd_pct: Optional[float] = None
    benchmark_volatility_pct: Optional[float] = None
    mdd_reduction_vs_benchmark_pct: Optional[float] = None
    sample_confidence: str = "INSUFFICIENT"
    sample_trading_days: int = 0
    metrics_basis: Dict[str, str] = Field(default_factory=dict)


class EnginePerformanceResponse(BaseModel):
    scorecard: EngineScorecard
    executive_kpis: ExecutivePerformanceKPIs
    benchmarks: PublicBenchmarkReport
    bots: List[BotPerformanceRecord]
    diagnostics: List[EngineDiagnosticFinding]
    period: str
    mode: str
    bot_scope: str
    cached: bool = False
    cache_age_seconds: float = 0.0
    # Data coverage of the trades actually evaluated (ISO-8601, UTC). ``None``
    # when no trade falls inside the requested period/mode, so the UI can show
    # the true "as of" date instead of the browser's fetch time.
    data_as_of: Optional[str] = None
    data_oldest: Optional[str] = None
    # True when the supplied history hit the backend row cap, meaning older
    # trades may exist but were not evaluated (relevant to long windows).
    history_truncated: bool = False


# ---------------------------------------------------------------------------
# Scoring and Rating Logic
# ---------------------------------------------------------------------------

def compute_grade_from_score(score: int) -> str:
    """Map numeric score (0-100) to standard letter grade."""
    if score >= 93:
        return "A+"
    elif score >= 88:
        return "A"
    elif score >= 83:
        return "A-"
    elif score >= 78:
        return "B+"
    elif score >= 73:
        return "B"
    elif score >= 68:
        return "B-"
    elif score >= 60:
        return "C+"
    elif score >= 50:
        return "C"
    else:
        return "D"


def compute_sample_confidence(trade_count: int, distinct_days: int) -> str:
    """Classify how much the sample can be trusted for a letter grade."""
    if trade_count < MIN_TRADES_FOR_GRADE or distinct_days < MIN_DAYS_FOR_GRADE:
        return "INSUFFICIENT"
    if trade_count < ADEQUATE_TRADES or distinct_days < ADEQUATE_DAYS:
        return "LOW"
    return "ADEQUATE"


def _band_at_least(value: Optional[float], bands: List[Tuple[float, int]], default: int) -> Optional[int]:
    """For 'higher is better' metrics. ``bands`` thresholds must be descending."""
    if value is None:
        return None
    for threshold, score in bands:
        if value >= threshold:
            return score
    return default


def _band_at_most(value: Optional[float], bands: List[Tuple[float, int]], default: int) -> Optional[int]:
    """For 'lower is better' metrics. ``bands`` thresholds must be ascending."""
    if value is None:
        return None
    for threshold, score in bands:
        if value <= threshold:
            return score
    return default


def _renormalized_score(weighted: List[Tuple[Optional[int], float]]) -> Optional[int]:
    """Weighted average over only the metrics that are actually available."""
    available = [(s, w) for s, w in weighted if s is not None]
    total_weight = sum(w for _, w in available)
    if not available or total_weight <= 0:
        return None
    score = sum(s * w for s, w in available) / total_weight
    return max(0, min(100, round(score)))


def score_execution_quality(
    fill_confirmation_rate_pct: Optional[float],
    reconciliation_rate_pct: Optional[float],
    cost_drag_pct: Optional[float],
) -> Optional[int]:
    """
    Score execution integrity from *measured* inputs only.

    ``fill_confirmation_rate_pct``: share of exits backed by a real IBKR fill.
    ``reconciliation_rate_pct``: share of validated trades whose bot P&L matched IBKR.
    ``cost_drag_pct``: (commissions + reported slippage) / gross wins.

    Returns ``None`` when no component can be measured (never a fabricated value).
    """
    s_fill = _band_at_least(fill_confirmation_rate_pct, [(98.0, 100), (95.0, 85), (90.0, 70)], 40)
    s_rec = _band_at_least(reconciliation_rate_pct, [(99.0, 100), (95.0, 85), (90.0, 70)], 40)
    s_cost = _band_at_most(cost_drag_pct, [(10.0, 100), (18.0, 85), (25.0, 70)], 45)
    return _renormalized_score([(s_fill, 0.40), (s_rec, 0.30), (s_cost, 0.30)])


def score_risk_adjusted_returns(
    sharpe: Optional[float],
    sortino: Optional[float],
    calmar: Optional[float],
) -> Optional[int]:
    """Score risk-adjusted return profile. Unmeasurable ratios are excluded."""
    s_sharpe = _band_at_least(sharpe, [(2.0, 100), (1.5, 88), (1.0, 75), (0.5, 60)], 35)
    s_sortino = _band_at_least(sortino, [(2.5, 100), (1.8, 88), (1.2, 75), (0.8, 60)], 40)
    s_calmar = _band_at_least(calmar, [(2.0, 100), (1.5, 88), (1.0, 75), (0.5, 60)], 35)
    return _renormalized_score([(s_sharpe, 0.40), (s_sortino, 0.35), (s_calmar, 0.25)])


def score_expectancy_and_edge(
    profit_factor: Optional[float],
    win_rate_pct: float,
    payoff_ratio: Optional[float],
    expectancy_dollars: float,
) -> Optional[int]:
    """Score statistical edge and expectancy."""
    s_pf = _band_at_least(profit_factor, [(2.0, 100), (1.6, 90), (1.3, 75), (1.0, 55)], 30)
    s_exp = _band_at_least(expectancy_dollars, [(25.0, 100), (0.01, 80), (-10.0, 50)], 25)

    if payoff_ratio is not None and win_rate_pct >= 55.0 and payoff_ratio >= 1.2:
        s_edge = 95
    elif payoff_ratio is not None and payoff_ratio >= 1.8:  # Trend follower profile
        s_edge = 85
    elif win_rate_pct >= 60.0:  # High win rate
        s_edge = 85
    elif win_rate_pct >= 45.0:
        s_edge = 70
    else:
        s_edge = 45

    return _renormalized_score([(s_pf, 0.45), (s_exp, 0.30), (s_edge, 0.25)])


def score_capital_preservation(
    max_drawdown_pct: Optional[float],
    mdd_reduction_vs_benchmark_pct: Optional[float],
) -> Optional[int]:
    """Score drawdown protection and capital preservation relative to Buy & Hold."""
    abs_dd = abs(max_drawdown_pct) if max_drawdown_pct is not None else None
    s_dd = _band_at_most(abs_dd, [(4.0, 100), (8.0, 90), (12.0, 80), (16.0, 65), (22.0, 45)], 25)
    s_rel = _band_at_least(
        mdd_reduction_vs_benchmark_pct, [(50.0, 100), (30.0, 85), (10.0, 70), (0.0, 60)], 40
    )
    return _renormalized_score([(s_dd, 0.60), (s_rel, 0.40)])


# ---------------------------------------------------------------------------
# Measurement helpers (derive real execution metrics from logged trades)
# ---------------------------------------------------------------------------

def compute_annualized_return_pct(daily_returns: List[float]) -> Optional[float]:
    """Annualized *compound* return (%) from daily returns, using trading-day scaling."""
    n = len(daily_returns)
    if n == 0:
        return None
    growth = 1.0
    for r in daily_returns:
        growth *= (1.0 + r)
    if growth <= 0.0:
        return None
    return round((growth ** (TRADING_DAYS_PER_YEAR / n) - 1.0) * 100.0, 2)


def compute_annualized_volatility_pct(daily_returns: List[float]) -> Optional[float]:
    """Annualized realized volatility (%) from daily returns."""
    n = len(daily_returns)
    if n < 2:
        return None
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    if variance <= 0:
        return None
    return round(math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 2)


def _extract_slippage(trade: Dict[str, Any]) -> Optional[float]:
    """Return an explicitly recorded slippage value, or ``None`` if not present.

    The engine never invents a slippage estimate; missing data stays missing.
    """
    slip = trade.get("slippage")
    if slip is None:
        validation = trade.get("validation")
        if isinstance(validation, dict):
            slip = validation.get("slippage")
    if slip is None:
        return None
    try:
        return abs(float(slip))
    except (ValueError, TypeError):
        return None


def _extract_quantity(trade: Dict[str, Any]) -> Optional[float]:
    """Return the logged share quantity if present, else ``None``."""
    meta = trade.get("analysis_metadata")
    if isinstance(meta, dict) and meta.get("quantity") is not None:
        try:
            return float(meta["quantity"])
        except (ValueError, TypeError):
            return None
    return None


def _is_fill_confirmed(trade: Dict[str, Any]) -> bool:
    """True when the exit price is backed by a real IBKR execution.

    Trades flagged as estimated from the signal price (``unconfirmedFill`` /
    ``exitPriceSource == "signal_fallback"``) count as unconfirmed. Legacy
    trades that never carried these fields are treated as confirmed.
    """
    if trade.get("unconfirmedFill") is True:
        return False
    meta = trade.get("analysis_metadata")
    if isinstance(meta, dict) and meta.get("unconfirmed_fill") is True:
        return False
    if trade.get("exitPriceSource") == "signal_price" or trade.get("exitPriceSource") == "signal_fallback":
        return False
    return True


def _first_present(trade: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    for key in keys:
        val = trade.get(key)
        if isinstance(val, dict):
            return val
    return None


def _format_value(value: Optional[float], suffix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}{suffix}"


def _compare_status(engine_value: Optional[float], benchmark_value: Optional[float],
                    higher_is_better: bool = True) -> str:
    if engine_value is None or benchmark_value is None:
        return "UNMEASURED"
    if higher_is_better:
        return "BEATING" if engine_value >= benchmark_value else "LAGGING"
    return "BEATING" if engine_value <= benchmark_value else "LAGGING"


# ---------------------------------------------------------------------------
# Core Engine Performance Calculator
# ---------------------------------------------------------------------------

def calculate_engine_performance(
    trades: List[Dict[str, Any]],
    bot_overview_data: Dict[str, Any],
    bot_configs: List[Dict[str, Any]],
    account_overview_data: Optional[Dict[str, Any]] = None,
    period: str = "YTD",
    bot_scope: str = "ALL",
    mode: str = "all",
    benchmark_symbol: str = "SPY",
    benchmark_price_series: Optional[List[float]] = None,
    history_limit: Optional[int] = None,
    now: Optional[datetime] = None
) -> EnginePerformanceResponse:
    """
    Evaluates engine performance per bot and across the overall portfolio.

    ``benchmark_price_series`` is an optional list of daily closing prices for
    ``benchmark_symbol`` covering the period. When supplied, buy-and-hold
    drawdown and volatility are computed from real data; otherwise they are
    reported as ``None`` (basis ``unavailable``) rather than fabricated.

    Produces the scorecard, public benchmark comparisons, and actionable
    diagnostics. Metrics that cannot be measured are returned as ``None``.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    norm_mode = (mode or "all").lower().strip()
    if norm_mode not in ("paper", "live", "all", "both"):
        norm_mode = "all"
    if norm_mode == "both":
        norm_mode = "all"

    series_key = len(benchmark_price_series) if benchmark_price_series else 0
    # Check cache
    cache_key = f"perf_{period}_{bot_scope}_{benchmark_symbol}_{norm_mode}_{series_key}"
    with _perf_cache_lock:
        if cache_key in _perf_cache:
            entry = _perf_cache[cache_key]
            age = (now - entry["timestamp"]).total_seconds()
            if age < CACHE_TTL_SECONDS:
                cached_resp: EnginePerformanceResponse = entry["data"].model_copy(deep=True)
                cached_resp.cached = True
                cached_resp.cache_age_seconds = round(age, 1)
                return cached_resp

    period_start = get_period_start_date(period, now)

    # Filter trades
    filtered_trades: List[Dict[str, Any]] = []
    for t in trades:
        ts = parse_iso_datetime(t.get("timestamp"))
        if not ts or ts < period_start:
            continue

        trade_mode = (t.get("trading_mode") or "").lower().strip()
        if norm_mode == "paper" and trade_mode != "paper":
            continue
        if norm_mode == "live" and trade_mode != "live":
            continue

        filtered_trades.append(t)

    # Data freshness/coverage of the trades actually evaluated. This is what the
    # UI should show as "as of" instead of the browser fetch time; it stays None
    # when nothing is in scope so no freshness is ever fabricated.
    _ok_ts = [ts for ts in (parse_iso_datetime(t.get("timestamp")) for t in filtered_trades) if ts]
    data_as_of = max(_ok_ts).isoformat() if _ok_ts else None
    data_oldest = min(_ok_ts).isoformat() if _ok_ts else None

    # The supplied history is capped by the backend query; if we received exactly
    # the cap, older trades may exist but were not evaluated.
    history_truncated = bool(history_limit is not None and len(trades) >= history_limit)

    # Normalize configs
    configs_list = list(bot_configs.values()) if isinstance(bot_configs, dict) else (bot_configs or [])
    config_by_sym = {c.get("symbol", "").upper(): c for c in configs_list if c.get("symbol")}

    # Universe = configured symbols + symbols that actually traded (no synthetic defaults)
    trade_symbols = {
        (t.get("symbol") or t.get("botName") or "").upper().strip() for t in filtered_trades
    }
    trade_symbols.discard("")
    all_symbols = sorted(set(config_by_sym.keys()) | trade_symbols)

    # Bucket trades per symbol
    trades_by_sym: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in all_symbols}
    for t in filtered_trades:
        sym = (t.get("symbol") or t.get("botName") or "").upper().strip()
        if not sym:
            continue
        trades_by_sym.setdefault(sym, []).append(t)

    # -----------------------------------------------------------------------
    # Benchmark series (real data when provided)
    # -----------------------------------------------------------------------
    benchmark_mdd: Optional[float] = None
    benchmark_volatility: Optional[float] = None
    benchmark_basis = "unavailable"
    if benchmark_price_series and len(benchmark_price_series) >= 2:
        benchmark_mdd = calculate_max_drawdown(benchmark_price_series)
        bench_returns = [
            (benchmark_price_series[i] - benchmark_price_series[i - 1]) / benchmark_price_series[i - 1]
            for i in range(1, len(benchmark_price_series))
            if benchmark_price_series[i - 1]
        ]
        benchmark_volatility = compute_annualized_volatility_pct(bench_returns)
        benchmark_basis = "measured"

    # Calculate metrics per bot.
    # Per-symbol aggregates are collected here so the portfolio view can later
    # be re-scoped to a single bot without mixing in other bots' trades.
    bot_records: List[BotPerformanceRecord] = []
    sym_daily_pnl: Dict[str, Dict[str, float]] = {}
    sym_aggregates: Dict[str, Dict[str, Any]] = {}

    for sym, sym_trades in trades_by_sym.items():
        cfg = config_by_sym.get(sym, {})
        bot_id = cfg.get("name", f"{sym.lower()}_sma")
        strategy = cfg.get("strategy", "sma_crossover")
        try:
            alloc_cap = float(cfg.get("allocated_capital") or cfg.get("capital") or 0.0)
        except (ValueError, TypeError):
            alloc_cap = 0.0

        if sym not in config_by_sym:
            # Traded historically but no longer present in the bot config. The
            # engine keeps the historical record (so removed bots still score)
            # but labels it clearly instead of implying the bot is still live.
            sym_status = "REMOVED"
        else:
            sym_status = "ACTIVE" if cfg.get("enabled", True) else "PAUSED"
        sym_mode = "PAPER" if norm_mode == "paper" else ("LIVE" if norm_mode == "live" else "BOTH")

        trades_count = len(sym_trades)
        wins = 0
        losses = 0
        gross_wins = 0.0
        gross_losses = 0.0
        sym_commissions = 0.0
        sym_slippage = 0.0
        sym_slippage_shares = 0.0
        sym_reported_slippage_trades = 0
        sym_fill_confirmed = 0
        sym_validated = 0
        sym_matched = 0
        daily_sym_pnl: Dict[str, float] = {}

        for tr in sym_trades:
            # P&L
            pnl = float(tr.get("profitLoss") or tr.get("total_pnl")
                        or (tr.get("stock_pnl", 0) + tr.get("option_pnl", 0)) or 0.0)
            comm = estimate_trade_commission(tr)
            sym_commissions += comm

            # Real slippage only when explicitly recorded
            slip = _extract_slippage(tr)
            qty = _extract_quantity(tr)
            if slip is not None:
                shares = qty if (qty is not None and qty > 0) else 0.0
                sym_slippage += slip * shares
                sym_slippage_shares += shares
                sym_reported_slippage_trades += 1

            # Execution integrity
            if _is_fill_confirmed(tr):
                sym_fill_confirmed += 1
            validation = _first_present(tr, ("validation",))
            if validation and validation.get("validated"):
                sym_validated += 1
                if validation.get("matches"):
                    sym_matched += 1

            if pnl > 0:
                wins += 1
                gross_wins += pnl
            elif pnl < 0:
                losses += 1
                gross_losses += pnl

            # Daily bucket for Sharpe
            t_dt = parse_iso_datetime(tr.get("timestamp"))
            if t_dt:
                day_key = t_dt.strftime("%Y-%m-%d")
                daily_sym_pnl[day_key] = daily_sym_pnl.get(day_key, 0.0) + (pnl - comm)

        # Overview data if present
        overview_info = bot_overview_data.get(sym, {}) if isinstance(bot_overview_data, dict) else {}
        unrealized_pnl = float(overview_info.get("unrealizedPnl") or 0.0)
        realized_pnl = float(overview_info.get("realizedPnl") or (gross_wins + gross_losses))
        net_pnl = realized_pnl + unrealized_pnl - sym_commissions

        sym_daily_pnl[sym] = daily_sym_pnl
        sym_aggregates[sym] = {
            "gross_wins": gross_wins,
            "gross_losses": gross_losses,
            "commissions": sym_commissions,
            "net_pnl": net_pnl,
            "slippage": sym_slippage,
            "slippage_shares": sym_slippage_shares,
            "fill_confirmed": sym_fill_confirmed,
            "validated": sym_validated,
            "matched": sym_matched,
            "trades_count": trades_count,
            "wins": wins,
            "losses": losses,
        }

        win_rate = round((wins / trades_count) * 100.0, 1) if trades_count > 0 else 0.0
        pf = calculate_profit_factor(gross_wins, gross_losses)

        avg_win = (gross_wins / wins) if wins > 0 else 0.0
        avg_loss = (abs(gross_losses) / losses) if losses > 0 else 0.0
        payoff = round(avg_win / avg_loss, 2) if avg_loss > 0 else (9.99 if avg_win > 0 else None)

        p_win = wins / trades_count if trades_count > 0 else 0.0
        p_loss = losses / trades_count if trades_count > 0 else 0.0
        expectancy_dollars = round(
            (p_win * avg_win) - (p_loss * avg_loss) - (sym_commissions / max(1, trades_count)), 2
        )
        expectancy_pct = round((expectancy_dollars / alloc_cap) * 100.0, 2) if alloc_cap > 0 else 0.0

        # Sharpe & Sortino (only with enough distinct daily observations)
        sorted_days = sorted(daily_sym_pnl.keys())
        daily_returns = [daily_sym_pnl[d] / alloc_cap for d in sorted_days] if alloc_cap > 0 else []
        if len(daily_returns) >= MIN_OBSERVATIONS_FOR_RATIOS:
            sym_sharpe, sym_sortino = calculate_sharpe_and_sortino(daily_returns)
        else:
            sym_sharpe, sym_sortino = None, None

        # Drawdown (needs at least one return observation)
        sym_mdd: Optional[float] = None
        sym_calmar: Optional[float] = None
        if daily_returns:
            nav_curve = [alloc_cap]
            curr_nav = alloc_cap
            for r in daily_returns:
                curr_nav *= (1.0 + r)
                nav_curve.append(curr_nav)
            sym_mdd = calculate_max_drawdown(nav_curve)
            ann_return = compute_annualized_return_pct(daily_returns)
            if ann_return is not None and abs(sym_mdd) > 0:
                sym_calmar = round(ann_return / abs(sym_mdd), 2)

        avg_slippage_sh: Optional[float] = None
        if sym_slippage_shares > 0:
            avg_slippage_sh = round(sym_slippage / sym_slippage_shares, 3)

        cost_drag: Optional[float] = None
        if gross_wins > 0:
            cost_drag = round(((sym_commissions + sym_slippage) / gross_wins) * 100.0, 1)

        fill_conf_rate = round((sym_fill_confirmed / trades_count) * 100.0, 1) if trades_count > 0 else None
        recon_rate = round((sym_matched / sym_validated) * 100.0, 1) if sym_validated > 0 else None

        sym_mdd_reduction: Optional[float] = None
        if sym_mdd is not None and benchmark_mdd is not None and abs(benchmark_mdd) > 0:
            sym_mdd_reduction = round(
                ((abs(benchmark_mdd) - abs(sym_mdd)) / abs(benchmark_mdd)) * 100.0, 1
            )

        # Bot rating score (renormalized over available pillars)
        b_exec = score_execution_quality(fill_conf_rate, recon_rate, cost_drag)
        b_risk = score_risk_adjusted_returns(sym_sharpe, sym_sortino, sym_calmar)
        b_exp = score_expectancy_and_edge(pf, win_rate, payoff, expectancy_dollars) if trades_count > 0 else None
        b_cap = score_capital_preservation(sym_mdd, sym_mdd_reduction)
        bot_score = _renormalized_score(
            [(b_exec, 0.25), (b_risk, 0.25), (b_exp, 0.25), (b_cap, 0.25)]
        )

        # Status badge and diagnostic
        if trades_count < MIN_TRADES_PER_BOT_FOR_GRADE or bot_score is None:
            status_badge = "LOW_SAMPLE"
            bot_grade: str = "N/A"
            bot_grade_score: Optional[int] = None
            diag = (
                f"Only {trades_count} trade(s) in period - below the "
                f"{MIN_TRADES_PER_BOT_FOR_GRADE}-trade minimum for a rating."
            )
        else:
            bot_grade_score = bot_score
            bot_grade = compute_grade_from_score(bot_score)
            if cost_drag is not None and cost_drag > 25.0:
                status_badge = "NEEDS_TUNING"
                diag = f"High friction drag ({cost_drag}% of measured gross wins). Switch to adaptive limit orders."
            elif bot_score >= 80:
                status_badge = "OPTIMAL"
                diag = f"Strong measured alpha; max drawdown {sym_mdd}% over the period."
            else:
                status_badge = "HEALTHY"
                diag = "Consistent baseline performance within target operating tolerances."

        roc = round((net_pnl / alloc_cap) * 100.0, 2) if alloc_cap > 0 else 0.0

        bot_records.append(
            BotPerformanceRecord(
                bot_id=bot_id,
                symbol=sym,
                strategy=strategy,
                trading_mode=sym_mode,
                status=sym_status,
                allocated_capital=alloc_cap,
                trades_count=trades_count,
                winning_trades=wins,
                losing_trades=losses,
                win_rate_pct=win_rate,
                profit_factor=pf,
                payoff_ratio=payoff,
                expectancy_dollars=expectancy_dollars,
                expectancy_pct=expectancy_pct,
                gross_pnl=round(gross_wins + gross_losses, 2),
                commissions=round(sym_commissions, 2),
                net_pnl=round(net_pnl, 2),
                realized_pnl=round(realized_pnl, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                roc_pct=roc,
                sharpe_ratio=sym_sharpe,
                sortino_ratio=sym_sortino,
                calmar_ratio=sym_calmar,
                max_drawdown_pct=sym_mdd,
                avg_slippage_per_share=avg_slippage_sh,
                slippage_sample_size=sym_reported_slippage_trades,
                cost_drag_pct=cost_drag,
                fill_confirmation_rate_pct=fill_conf_rate,
                reconciliation_rate_pct=recon_rate,
                rating_grade=bot_grade,
                rating_score=bot_grade_score,
                status_badge=status_badge,
                diagnostic_finding=diag,
            )
        )

    # -----------------------------------------------------------------------
    # Portfolio or Single-Bot Scoped Aggregation
    # -----------------------------------------------------------------------
    target_scope = (bot_scope or "ALL").upper().strip()
    # Resolve the requested scope against both the ticker symbol and the bot id,
    # case-insensitively, so a bot can be selected by either identifier. Bots
    # are added and removed over time, so an unresolvable scope must never
    # silently blend every bot's trades into what the caller believes is a
    # single-bot view: we fall back to the full portfolio *and* flag it.
    scoped_bot: Optional[BotPerformanceRecord] = None
    unresolved_scope = False
    if target_scope and target_scope != "ALL":
        scoped_bot = next(
            (
                b for b in bot_records
                if b.symbol.upper() == target_scope
                or (b.bot_id or "").upper() == target_scope
            ),
            None,
        )
        if scoped_bot is None:
            unresolved_scope = True

    scope_symbols = [scoped_bot.symbol] if scoped_bot else [b.symbol for b in bot_records]
    # Echo the scope that was actually applied (an unresolved request collapses
    # to ALL) so the response never claims a single-bot view it did not produce.
    resolved_scope = scoped_bot.symbol if scoped_bot else "ALL"

    # Aggregate only the in-scope symbols so a single-bot view never blends in
    # other bots' trades, drawdown, or execution integrity.
    total_gross_wins = 0.0
    total_gross_losses = 0.0
    total_commissions = 0.0
    total_net_pnl = 0.0
    total_slippage_dollars = 0.0
    total_slippage_shares = 0.0
    total_fill_confirmed = 0
    total_validated = 0
    total_matched = 0
    total_trades_count = 0
    total_wins = 0
    total_losses = 0
    daily_portfolio_pnl: Dict[str, float] = {}

    for sym in scope_symbols:
        agg = sym_aggregates.get(sym, {})
        total_gross_wins += agg.get("gross_wins", 0.0)
        total_gross_losses += agg.get("gross_losses", 0.0)
        total_commissions += agg.get("commissions", 0.0)
        total_net_pnl += agg.get("net_pnl", 0.0)
        total_slippage_dollars += agg.get("slippage", 0.0)
        total_slippage_shares += agg.get("slippage_shares", 0.0)
        total_fill_confirmed += agg.get("fill_confirmed", 0)
        total_validated += agg.get("validated", 0)
        total_matched += agg.get("matched", 0)
        total_trades_count += agg.get("trades_count", 0)
        total_wins += agg.get("wins", 0)
        total_losses += agg.get("losses", 0)
        for day, value in sym_daily_pnl.get(sym, {}).items():
            daily_portfolio_pnl[day] = daily_portfolio_pnl.get(day, 0.0) + value

    overall_win_rate = round((total_wins / total_trades_count) * 100.0, 1) if total_trades_count > 0 else 0.0
    overall_pf = calculate_profit_factor(total_gross_wins, total_gross_losses)
    avg_total_win = (total_gross_wins / total_wins) if total_wins > 0 else 0.0
    avg_total_loss = (abs(total_gross_losses) / total_losses) if total_losses > 0 else 0.0
    overall_payoff = round(avg_total_win / avg_total_loss, 2) if avg_total_loss > 0 else None

    tot_p_win = total_wins / total_trades_count if total_trades_count > 0 else 0.0
    tot_p_loss = total_losses / total_trades_count if total_trades_count > 0 else 0.0
    overall_expectancy = round(
        (tot_p_win * avg_total_win) - (tot_p_loss * avg_total_loss)
        - (total_commissions / max(1, total_trades_count)),
        2
    )

    total_allocated = sum(
        next((b.allocated_capital for b in bot_records if b.symbol == s), 0.0) for s in scope_symbols
    )
    current_nav = total_allocated + total_net_pnl
    # Account-level NAV only applies to the whole-portfolio view, never a single bot.
    if not scoped_bot and account_overview_data and "net_liquidation_value" in account_overview_data:
        try:
            current_nav = float(account_overview_data["net_liquidation_value"])
        except (ValueError, TypeError):
            pass

    # Daily observation count (for confidence + ratio gating)
    sorted_all_days = sorted(daily_portfolio_pnl.keys())
    portfolio_daily_returns = (
        [daily_portfolio_pnl[d] / total_allocated for d in sorted_all_days]
        if total_allocated > 0 else []
    )
    distinct_days = len(portfolio_daily_returns)

    # Portfolio Sharpe & Sortino - only with enough observations (no fallbacks)
    if distinct_days >= MIN_OBSERVATIONS_FOR_RATIOS:
        overall_sharpe, overall_sortino = calculate_sharpe_and_sortino(portfolio_daily_returns)
    else:
        overall_sharpe, overall_sortino = None, None

    # Overall Drawdown
    overall_mdd: Optional[float] = None
    if portfolio_daily_returns:
        all_nav_curve = [total_allocated]
        c_nav = total_allocated
        for r in portfolio_daily_returns:
            c_nav *= (1.0 + r)
            all_nav_curve.append(c_nav)
        overall_mdd = calculate_max_drawdown(all_nav_curve)

    # Overall Calmar
    overall_calmar: Optional[float] = None
    if overall_mdd is not None and abs(overall_mdd) > 0 and distinct_days >= MIN_OBSERVATIONS_FOR_RATIOS:
        ann_portfolio_return = compute_annualized_return_pct(portfolio_daily_returns)
        if ann_portfolio_return is not None:
            overall_calmar = round(ann_portfolio_return / abs(overall_mdd), 2)

    # Benchmark-relative drawdown reduction
    mdd_reduction_pct: Optional[float] = None
    if overall_mdd is not None and benchmark_mdd is not None and abs(benchmark_mdd) > 0:
        mdd_reduction_pct = round(
            ((abs(benchmark_mdd) - abs(overall_mdd)) / abs(benchmark_mdd)) * 100.0, 1
        )

    # Overall execution metrics (measured)
    overall_fill_confirmation: Optional[float] = None
    if total_trades_count > 0:
        overall_fill_confirmation = round((total_fill_confirmed / total_trades_count) * 100.0, 1)
    overall_reconciliation: Optional[float] = None
    if total_validated > 0:
        overall_reconciliation = round((total_matched / total_validated) * 100.0, 1)

    avg_portfolio_slippage: Optional[float] = None
    if total_slippage_shares > 0:
        avg_portfolio_slippage = round(total_slippage_dollars / total_slippage_shares, 3)

    portfolio_cost_drag: Optional[float] = None
    if total_gross_wins > 0:
        portfolio_cost_drag = round(
            ((total_commissions + total_slippage_dollars) / total_gross_wins) * 100.0, 1
        )

    realized_volatility = compute_annualized_volatility_pct(portfolio_daily_returns)

    sample_confidence = compute_sample_confidence(total_trades_count, distinct_days)

    # -----------------------------------------------------------------------
    # Scoring the overall engine
    # -----------------------------------------------------------------------
    exec_score = score_execution_quality(overall_fill_confirmation, overall_reconciliation, portfolio_cost_drag)
    risk_score = score_risk_adjusted_returns(overall_sharpe, overall_sortino, overall_calmar)
    edge_score = score_expectancy_and_edge(
        overall_pf, overall_win_rate, overall_payoff, overall_expectancy
    ) if total_trades_count > 0 else None
    cap_score = score_capital_preservation(overall_mdd, mdd_reduction_pct)

    overall_score = _renormalized_score(
        [(exec_score, 0.25), (risk_score, 0.25), (edge_score, 0.25), (cap_score, 0.25)]
    )

    # Suppress a letter grade entirely when the sample is too thin.
    scope_label = f"Bot {scoped_bot.symbol}" if scoped_bot else "Trading engine"

    if sample_confidence == "INSUFFICIENT" or overall_score is None:
        overall_grade = "N/A"
        overall_score = None
        summary_text = (
            f"Insufficient data to grade {scope_label.lower() if scoped_bot else 'the engine'}: "
            f"{total_trades_count} trade(s) across {distinct_days} trading day(s) in {period}. "
            f"A rating requires at least {MIN_TRADES_FOR_GRADE} trades across "
            f"{MIN_DAYS_FOR_GRADE} days. Metrics below are shown only where they could be measured."
        )
    else:
        overall_grade = compute_grade_from_score(overall_score)
        cap_clause = (
            f"Max drawdown of {overall_mdd}% vs {benchmark_symbol} buy-and-hold {benchmark_mdd}%."
            if overall_mdd is not None and benchmark_mdd is not None
            else f"{benchmark_symbol} benchmark drawdown unavailable for comparison."
        )
        if scoped_bot:
            summary_text = (
                f"Bot {scoped_bot.symbol} ({scoped_bot.strategy}) operating at Grade {overall_grade} "
                f"({overall_score}/100) in {scoped_bot.trading_mode} mode on {total_trades_count} trade(s) "
                f"across {distinct_days} days ({sample_confidence} confidence). {cap_clause}"
            )
        else:
            summary_text = (
                f"Trading engine operating at Grade {overall_grade} ({overall_score}/100) on "
                f"{total_trades_count} trades across {distinct_days} days "
                f"({sample_confidence} confidence). {cap_clause}"
            )

    scorecard = EngineScorecard(
        overall_grade=overall_grade,
        overall_score=overall_score,
        execution_score=exec_score,
        risk_adjusted_score=risk_score,
        expectancy_score=edge_score,
        capital_preservation_score=cap_score,
        sample_confidence=sample_confidence,
        summary_assessment=summary_text,
    )

    metrics_basis = {
        "execution.fill_confirmation": "measured" if overall_fill_confirmation is not None else "unavailable",
        "execution.reconciliation": "measured" if overall_reconciliation is not None else "unavailable",
        "execution.slippage": "measured" if avg_portfolio_slippage is not None else "unavailable",
        "execution.effective_over_quoted": "unavailable",
        "execution.rejection_rate": "unavailable",
        "risk.sharpe": "measured" if overall_sharpe is not None else "unavailable",
        "risk.sortino": "measured" if overall_sortino is not None else "unavailable",
        "risk.calmar": "measured" if overall_calmar is not None else "unavailable",
        "capital.max_drawdown": "measured" if overall_mdd is not None else "unavailable",
        "capital.benchmark_mdd": benchmark_basis,
        "capital.realized_volatility": "measured" if realized_volatility is not None else "unavailable",
        "capital.benchmark_volatility": benchmark_basis,
        "edge.profit_factor": "measured" if overall_pf is not None else "unavailable",
    }

    executive_kpis = ExecutivePerformanceKPIs(
        nav=round(current_nav, 2),
        total_net_pnl=round(total_net_pnl, 2),
        total_trades=total_trades_count,
        win_rate_pct=overall_win_rate,
        profit_factor=overall_pf,
        payoff_ratio=overall_payoff,
        expectancy_dollars=overall_expectancy,
        annualized_sharpe=overall_sharpe,
        sortino_ratio=overall_sortino,
        calmar_ratio=overall_calmar,
        max_drawdown_pct=overall_mdd,
        avg_slippage_per_share=avg_portfolio_slippage,
        effective_over_quoted_ratio_pct=None,
        fill_rate_pct=None,
        rejection_rate_pct=None,
        fill_confirmation_rate_pct=overall_fill_confirmation,
        reconciliation_rate_pct=overall_reconciliation,
        transaction_cost_drag_pct=portfolio_cost_drag,
        realized_volatility_pct=realized_volatility,
        benchmark_mdd_pct=benchmark_mdd,
        benchmark_volatility_pct=benchmark_volatility,
        mdd_reduction_vs_benchmark_pct=mdd_reduction_pct,
        sample_confidence=sample_confidence,
        sample_trading_days=distinct_days,
        metrics_basis=metrics_basis,
    )

    # -----------------------------------------------------------------------
    # Public Benchmark Comparisons
    # -----------------------------------------------------------------------
    benchmarks = PublicBenchmarkReport(
        benchmark_source=benchmark_basis,
        sec_rule_605=[
            BenchmarkComparisonMetric(
                name="Fill Confirmation Rate",
                category="EXECUTION",
                engine_value=overall_fill_confirmation,
                engine_formatted=_format_value(overall_fill_confirmation, "%", 1),
                benchmark_name="IBKR Execution-Backed Fill Standard",
                benchmark_value=95.0,
                benchmark_formatted="> 95.0%",
                status=_compare_status(overall_fill_confirmation, 95.0, higher_is_better=True),
                data_basis="measured" if overall_fill_confirmation is not None else "unavailable",
                description="Share of exits whose price came from a real IBKR execution rather than a signal-price fallback.",
            ),
            BenchmarkComparisonMetric(
                name="P&L Reconciliation Rate",
                category="EXECUTION",
                engine_value=overall_reconciliation,
                engine_formatted=_format_value(overall_reconciliation, "%", 1),
                benchmark_name="Bot-vs-Broker Reconciliation Standard",
                benchmark_value=99.0,
                benchmark_formatted="> 99.0%",
                status=_compare_status(overall_reconciliation, 99.0, higher_is_better=True),
                data_basis="measured" if overall_reconciliation is not None else "unavailable",
                description="Share of validated trades whose bot-computed P&L matched the IBKR realized P&L.",
            ),
            BenchmarkComparisonMetric(
                name="Effective-over-Quoted (E/Q) Spread Ratio",
                category="EXECUTION",
                engine_value=None,
                engine_formatted="N/A",
                benchmark_name="SEC Rule 605 Industry Standard",
                benchmark_value=100.0,
                benchmark_formatted="< 100.0%",
                status="UNMEASURED",
                data_basis="unavailable",
                description="Requires per-order bid/ask quote capture, which the trade log does not currently store.",
            ),
            BenchmarkComparisonMetric(
                name="Order Rejection Rate",
                category="EXECUTION",
                engine_value=None,
                engine_formatted="N/A",
                benchmark_name="Institutional DMA Benchmark",
                benchmark_value=0.10,
                benchmark_formatted="< 0.10%",
                status="UNMEASURED",
                data_basis="unavailable",
                description="Requires broker order-reject events, which the trade log does not currently store.",
            ),
        ],
        institutional_cta=[
            BenchmarkComparisonMetric(
                name="Annualized Sharpe Ratio",
                category="RISK_ADJUSTED",
                engine_value=overall_sharpe,
                engine_formatted=_format_value(overall_sharpe, "", 2),
                benchmark_name="Barclay CTA Index Median",
                benchmark_value=1.10,
                benchmark_formatted="1.10",
                status=_compare_status(overall_sharpe, 1.10, higher_is_better=True),
                data_basis="measured" if overall_sharpe is not None else "unavailable",
                description="Excess return per unit of volatility vs Barclay CTA systematic fund benchmark.",
            ),
            BenchmarkComparisonMetric(
                name="Sortino Ratio (Downside Risk)",
                category="RISK_ADJUSTED",
                engine_value=overall_sortino,
                engine_formatted=_format_value(overall_sortino, "", 2),
                benchmark_name="HFRX Systematic Diversified Benchmark",
                benchmark_value=1.50,
                benchmark_formatted="1.50",
                status=_compare_status(overall_sortino, 1.50, higher_is_better=True),
                data_basis="measured" if overall_sortino is not None else "unavailable",
                description="Risk-adjusted return penalizing only downside volatility.",
            ),
            BenchmarkComparisonMetric(
                name="Calmar Ratio (Return / Max DD)",
                category="CAPITAL_PRESERVATION",
                engine_value=overall_calmar,
                engine_formatted=_format_value(overall_calmar, "", 2),
                benchmark_name="Barclay Quantitative Traders Benchmark",
                benchmark_value=1.00,
                benchmark_formatted="> 1.00",
                status=_compare_status(overall_calmar, 1.00, higher_is_better=True),
                data_basis="measured" if overall_calmar is not None else "unavailable",
                description="Annualized compound return divided by maximum historical drawdown.",
            ),
            BenchmarkComparisonMetric(
                name="Profit Factor (Gross Wins / Losses)",
                category="RISK_ADJUSTED",
                engine_value=overall_pf,
                engine_formatted=_format_value(overall_pf, "", 2),
                benchmark_name="Systematic Trading Standard",
                benchmark_value=1.40,
                benchmark_formatted="> 1.40",
                status=_compare_status(overall_pf, 1.40, higher_is_better=True),
                data_basis="measured" if overall_pf is not None else "unavailable",
                description="Ratio of gross trading profits to gross trading losses across completed cycles.",
            ),
        ],
        buy_and_hold=[
            BenchmarkComparisonMetric(
                name="Max Drawdown Protection",
                category="CAPITAL_PRESERVATION",
                engine_value=overall_mdd,
                engine_formatted=_format_value(overall_mdd, "%", 2),
                benchmark_name=f"{benchmark_symbol} Buy & Hold Max DD",
                benchmark_value=benchmark_mdd,
                benchmark_formatted=_format_value(benchmark_mdd, "%", 2),
                status=_compare_status(
                    abs(overall_mdd) if overall_mdd is not None else None,
                    abs(benchmark_mdd) if benchmark_mdd is not None else None,
                    higher_is_better=False,
                ),
                data_basis="measured" if (overall_mdd is not None and benchmark_mdd is not None) else "unavailable",
                description=(
                    f"Engine reduced maximum drawdown by {mdd_reduction_pct}% relative to holding "
                    f"{benchmark_symbol}." if mdd_reduction_pct is not None
                    else f"Requires {benchmark_symbol} price history and engine drawdown data."
                ),
            ),
            BenchmarkComparisonMetric(
                name="Realized Volatility",
                category="CAPITAL_PRESERVATION",
                engine_value=realized_volatility,
                engine_formatted=_format_value(realized_volatility, "% vol", 1),
                benchmark_name=f"{benchmark_symbol} Realized Volatility",
                benchmark_value=benchmark_volatility,
                benchmark_formatted=_format_value(benchmark_volatility, "% vol", 1),
                status=_compare_status(realized_volatility, benchmark_volatility, higher_is_better=False),
                data_basis="measured" if (realized_volatility is not None and benchmark_volatility is not None) else "unavailable",
                description="Annualized realized volatility of engine daily returns vs the buy-and-hold benchmark.",
            ),
        ],
    )

    # -----------------------------------------------------------------------
    # Automated Diagnostic Findings & Actionable Next Levers
    # -----------------------------------------------------------------------
    diagnostics: List[EngineDiagnosticFinding] = []

    if sample_confidence == "INSUFFICIENT":
        diagnostics.append(
            EngineDiagnosticFinding(
                id="diag-insufficient-sample",
                level="INFO",
                category="SIGNAL_FUNNEL",
                title="Insufficient Sample to Rate the Engine",
                detail=(
                    f"{total_trades_count} trade(s) across {distinct_days} trading day(s) in {period}. "
                    f"A letter grade requires at least {MIN_TRADES_FOR_GRADE} trades across "
                    f"{MIN_DAYS_FOR_GRADE} days, and risk ratios require "
                    f"{MIN_OBSERVATIONS_FOR_RATIOS} daily observations."
                ),
                recommended_action="Allow the engine to accumulate a larger live/paper sample before drawing conclusions.",
                guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#pillar-2-quantitative--strategy-rating-parameters",
            )
        )
    else:
        if mdd_reduction_pct is not None and mdd_reduction_pct >= 30.0:
            diagnostics.append(
                EngineDiagnosticFinding(
                    id="diag-cap-preservation",
                    level="SUCCESS",
                    category="RISK_PRESERVATION",
                    title="Drawdown Suppression vs Benchmark",
                    detail=(
                        f"Engine Max Drawdown is contained at {overall_mdd}%, a {mdd_reduction_pct}% "
                        f"reduction in peak-to-trough pain compared to holding {benchmark_symbol}."
                    ),
                    recommended_action="Maintain current ATR-based stop floor and break-even lock mechanisms.",
                    guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#pillar-2-quantitative--strategy-rating-parameters",
                )
            )
        elif mdd_reduction_pct is not None and mdd_reduction_pct < 0.0:
            diagnostics.append(
                EngineDiagnosticFinding(
                    id="diag-cap-underperform",
                    level="WARNING",
                    category="RISK_PRESERVATION",
                    title="Drawdown Exceeds Buy-and-Hold",
                    detail=(
                        f"Engine Max Drawdown ({overall_mdd}%) is deeper than {benchmark_symbol} "
                        f"buy-and-hold ({benchmark_mdd}%)."
                    ),
                    recommended_action="Tighten position sizing or ATR stop distance to reduce peak-to-trough exposure.",
                    guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#2-drawdown-control",
                )
            )

        if overall_fill_confirmation is not None and overall_fill_confirmation < 98.0:
            diagnostics.append(
                EngineDiagnosticFinding(
                    id="diag-unconfirmed-fills",
                    level="WARNING",
                    category="EXECUTION",
                    title="Unconfirmed Fill Prices Detected",
                    detail=(
                        f"{round(100.0 - overall_fill_confirmation, 1)}% of exits used a signal-price fallback "
                        f"rather than a logged IBKR execution, which can distort slippage and P&L."
                    ),
                    recommended_action="Investigate async fill capture timeouts so exit prices are taken from IBKR executions.",
                    guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#a-execution-quality-benchmarks-sec-rule-605--606-data",
                )
            )

        if overall_reconciliation is not None and overall_reconciliation < 99.0:
            diagnostics.append(
                EngineDiagnosticFinding(
                    id="diag-reconciliation",
                    level="WARNING",
                    category="EXECUTION",
                    title="Bot / Broker P&L Reconciliation Gaps",
                    detail=(
                        f"Only {overall_reconciliation}% of validated trades matched IBKR realized P&L."
                    ),
                    recommended_action="Review P&L calculator inputs (fees, partial fills) for the mismatched trades.",
                    guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#a-execution-quality-benchmarks-sec-rule-605--606-data",
                )
            )

        if overall_sharpe is not None:
            diagnostics.append(
                EngineDiagnosticFinding(
                    id="diag-risk-adjusted",
                    level="INFO" if overall_sharpe >= 1.0 else "WARNING",
                    category="RISK_PRESERVATION",
                    title=f"Annualized Sharpe of {overall_sharpe}",
                    detail=(
                        f"Measured over {distinct_days} daily observations against a 4.5% risk-free hurdle."
                    ),
                    recommended_action="Compare against the Barclay CTA median (1.10) when rebalancing risk exposure.",
                    guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#pillar-2-quantitative--strategy-rating-parameters",
                )
            )

    # Warning: the requested bot scope could not be resolved (bot removed or
    # renamed since the page was opened). Report the fallback explicitly.
    if unresolved_scope:
        diagnostics.append(
            EngineDiagnosticFinding(
                id="diag-unresolved-scope",
                level="WARNING",
                category="ATTRIBUTION",
                title=f"Bot '{target_scope}' not found",
                detail=(
                    f"No configured or traded bot matches '{target_scope}' in {period}. "
                    f"The view fell back to the full portfolio (ALL)."
                ),
                recommended_action="Pick a bot that still exists, or refresh the page so the bot list matches the current configuration.",
                guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md",
            )
        )

    # Opportunity: idle configured bots / thin signal funnel (data-driven)
    idle_symbols = [b.symbol for b in bot_records if b.trades_count == 0]
    if idle_symbols:
        diagnostics.append(
            EngineDiagnosticFinding(
                id="diag-idle-bots",
                level="OPPORTUNITY",
                category="SIGNAL_FUNNEL",
                title="Idle Configured Bots",
                detail=(
                    f"{len(idle_symbols)} configured bot(s) generated no trades in {period}: "
                    f"{', '.join(idle_symbols[:6])}{'...' if len(idle_symbols) > 6 else ''}."
                ),
                recommended_action="Calibrate confirmation floor / MTF filters using tune_bot_params.py to avoid over-filtering valid setups.",
                guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#1-reduce-slippage--adverse-selection-highest-pnl-impact",
            )
        )

    # Per-bot friction warnings (only where measured)
    for b in bot_records:
        if b.cost_drag_pct is not None and b.cost_drag_pct > 25.0 and b.trades_count >= MIN_TRADES_PER_BOT_FOR_GRADE:
            diagnostics.append(
                EngineDiagnosticFinding(
                    id=f"diag-cost-drag-{b.symbol.lower()}",
                    level="WARNING",
                    category="EXECUTION",
                    title=f"Elevated Friction Drag on {b.symbol}",
                    detail=(
                        f"{b.symbol} shows transaction cost drag of {b.cost_drag_pct}% of gross profits. "
                        f"Slippage and commission overhead are eroding alpha."
                    ),
                    recommended_action="Switch from aggressive market orders to patient limit orders or widen the take-profit ATR multiplier.",
                    guide_reference="guides/TRADING_ENGINE_EVALUATION_AND_BENCHMARKING_GUIDE.md#1-slippage--order-routing-optimization-highest-immediate-impact",
                )
            )

    response = EnginePerformanceResponse(
        scorecard=scorecard,
        executive_kpis=executive_kpis,
        benchmarks=benchmarks,
        bots=bot_records,
        diagnostics=diagnostics,
        period=period,
        mode=norm_mode,
        bot_scope=resolved_scope,
        cached=False,
        cache_age_seconds=0.0,
        data_as_of=data_as_of,
        data_oldest=data_oldest,
        history_truncated=history_truncated,
    )

    with _perf_cache_lock:
        _perf_cache[cache_key] = {"data": response, "timestamp": now}

    return response
