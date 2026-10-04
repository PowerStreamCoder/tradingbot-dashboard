"""
Expert Parameter Recommendation Engine for Automated Bot Provisioning.

Generates quantitatively sound, asset-specific trading bot parameters based on:
1. Asset Price & Capital Allocation (dynamic position sizing)
2. Daily Volatility Regime / 14-day ATR (trailing stop multiplier tuning)
3. Strategy Track (Track 1 Growth Equity vs Track 2 Covered Call Income)
4. Fundamental Solvency & Option Implied Volatility
"""

from typing import Dict, Any, Optional, List


def generate_expert_parameter_advice(
    symbol: str,
    strategy_track: str = "GROWTH",
    capital_allocation: float = 10000.0,
    current_price: Optional[float] = None,
    est_atr_pct: Optional[float] = None,
    implied_volatility: Optional[float] = None,
    solvency_rating: Optional[str] = None,
    is_degraded: bool = False,
    missing_sources: Optional[List[str]] = None,
    degradation_warnings: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Generate tailored bot parameters with quant rationale.

    Args:
        symbol: Equity ticker symbol (e.g. 'NVDA', 'KTOS')
        strategy_track: 'GROWTH' (Track 1) or 'INCOME' (Track 2)
        capital_allocation: Total USD capital assigned to bot
        current_price: Estimated share price
        est_atr_pct: Estimated 14-day ATR percentage (e.g. 0.025 for 2.5%)
        implied_volatility: Options implied volatility if available
        solvency_rating: Solvency health ('Pristine', 'Robust', 'Adequate', 'Distressed')
        is_degraded: Whether candidate data feeds were incomplete or degraded
        missing_sources: List of missing upstream data sources
        degradation_warnings: List of specific data degradation warnings

    Returns:
        Structured dictionary with recommended_params, expert_rationale, and risk_tier.
    """
    sym = symbol.upper().strip()
    track = (strategy_track or "GROWTH").upper()
    capital = max(1000.0, float(capital_allocation or 10000.0))
    price = max(1.0, float(current_price or 100.0))
    atr_pct = float(est_atr_pct or 0.02)  # default 2% ATR
    solvency = solvency_rating or "Adequate"

    # 1. Capital Allocation & Bucket Sizing
    # Typically 2 long buckets per group
    long_bucket_capital = round(capital / 2.0, 2)
    short_bucket_capital = 1000.0 if track == "GROWTH" else 0.0
    buckets_per_group = 2

    # Dynamic minimum shares: ensure single bucket can purchase at least min_shares
    # If stock is $400 and bucket is $5,000, 100 shares is impossible. Min shares should be max(1, bucket // price)
    calculated_shares = int(long_bucket_capital / price)
    position_min_shares = max(1, min(100, calculated_shares))

    # 2. Volatility-Adaptive Trailing Stop
    # High ATR (>3% daily) requires wider stop (2.8x) to prevent noise stopouts
    # Low ATR (<1.5% daily) benefits from tighter stop (1.6x)
    if atr_pct > 0.030:
        trailing_stop_multiplier = 2.8
        stop_desc = "Wider 2.8x ATR trailing stop selected to absorb high daily noise and prevent whipsaw exits."
    elif atr_pct < 0.015:
        trailing_stop_multiplier = 1.6
        stop_desc = "Tight 1.6x ATR trailing stop selected to secure capital in low-volatility trend regime."
    else:
        trailing_stop_multiplier = 2.2
        stop_desc = "Standard 2.2x ATR trailing stop selected for balanced trend tracking."

    # 3. Trend & Moving Average Windows
    if track == "GROWTH":
        sma_fast = 5
        sma_slow = 20
        sma_desc = "Fast 5/20 SMA crossover tuned for aggressive momentum capture."
    else:
        sma_fast = 10
        sma_slow = 30
        sma_desc = "Smoothed 10/30 SMA trend filter tuned to avoid false breakouts on income holdings."

    # 4. Covered Call Execution Mode & Expiry
    if track == "INCOME":
        cc_mode = "INCOME"
        call_expiration_days = 30
        strike_offset_pct = 4.0  # 4% OTM
        cc_desc = "Income mode (30-day expiry, ~4% OTM strike) optimizes theta decay and steady premium yield."
    else:
        cc_mode = "TOTAL_RETURN"
        call_expiration_days = 7
        strike_offset_pct = 9.0  # 9% OTM
        cc_desc = "Total Return mode (7-day expiry, ~9% OTM strike) preserves equity capital upside while monetizing weekly calls."

    # 5. Risk Tier Assessment
    if atr_pct >= 0.030 or solvency == "Distressed":
        risk_tier = "High"
    elif atr_pct < 0.015 and solvency in ("Pristine", "Robust") and not is_degraded:
        risk_tier = "Low"
    else:
        risk_tier = "Moderate"

    rationale_bullets: List[str] = [
        f"Position Sizing: Position min shares calibrated to {position_min_shares} shares (${position_min_shares * price:,.0f} min order) to match ${long_bucket_capital:,.0f} bucket limit at ${price:.2f}/share.",
        f"Volatility Risk ({atr_pct * 100:.1f}% ATR): {stop_desc}",
        f"Strategy Architecture: {sma_desc}",
        f"Covered Call Execution: {cc_desc}",
    ]

    if is_degraded:
        missing_text = ', '.join(missing_sources) if missing_sources else 'partial feeds'
        rationale_bullets.insert(
            0,
            f"⚠️ Data Degradation Alert: Sizing & risk parameters calibrated with unverified/missing inputs ({missing_text}). Operator discretion advised."
        )

    return {
        "symbol": sym,
        "strategy_track": track,
        "capital_allocation": capital,
        "current_price": price,
        "risk_tier": risk_tier,
        "is_degraded": is_degraded,
        "missing_sources": missing_sources or [],
        "degradation_warnings": degradation_warnings or [],
        "recommended_params": {
            "capital": {
                "capital_per_bucket_long": long_bucket_capital,
                "capital_per_bucket_short": short_bucket_capital,
                "buckets_per_group": buckets_per_group,
            },
            "atr_parameters": {
                "atr_period": 14,
                "trailing_stop_atr_multiplier": trailing_stop_multiplier,
                "position_min_shares": position_min_shares,
                "position_max_capital": round(capital * 0.95, 2),
            },
            "strategy": {
                "sma_fast": sma_fast,
                "sma_slow": sma_slow,
            },
            "covered_calls": {
                "use_covered_calls": True,
                "cc_mode": cc_mode,
                "call_expiration_days": call_expiration_days,
                "strike_offset_pct": strike_offset_pct,
            },
            "trading_behavior": {
                "allow_short_entries": (track == "GROWTH" and short_bucket_capital > 0),
            }
        },
        "expert_rationale": rationale_bullets,
        "advisory_summary": f"Quant recommendation for {sym}: Deploy ${capital:,.0f} across {buckets_per_group} buckets ({position_min_shares} min shares/order) with a {trailing_stop_multiplier}x ATR stop and {cc_mode} covered call rules." + (
            f" (⚠️ Note: inputs degraded due to missing {missing_text})" if is_degraded else ""
        )
    }
