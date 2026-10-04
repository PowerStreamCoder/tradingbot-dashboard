"""
Covered Call Income Screener for StockPicker (Track 2: INCOME).

Evaluates equities and ETFs for Covered Call income suitability based on:
1. Low realized volatility (ATR/Price between 0.4% and 3.5% per cc_suitability.py)
2. Attractive option premium yield (aiming for 1.8% - 3.5% monthly premium yield on 25-delta calls)
3. Healthy options liquidity (open interest and tight bid-ask spreads)
4. Earnings collision safety (distance to next earnings date > 30 days to avoid assignment shocks)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)
UTC = timezone.utc

YF_OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/{ticker}"
YF_QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Low-volatility, option-rich candidate universe for Covered Call Income scouting
INCOME_UNIVERSE = [
    "SPY", "IWM", "DIA", "XLF", "XLE", "XLV", "XLI", "XLP",
    "JNJ", "PG", "KO", "PFE", "VZ", "T", "BMY", "MRK", "CSCO", "INTC",
    "BAC", "WFC", "USB", "KMB", "DUK", "SO", "NEE", "MCD", "WMT"
]


def fetch_yahoo_options(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Fetch nearest monthly option chain from Yahoo Finance REST API.

    Returns:
        Dict with current price, expiration dates, and calls chain.
    """
    url = YF_OPTIONS_URL.format(ticker=ticker)
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            result = data.get("optionChain", {}).get("result", [])
            if result:
                return result[0]
    except Exception as e:
        logger.warning(f"[INCOME-SCREENER] Failed to fetch options for {ticker}: {e}")
    return None


def fetch_earnings_date(ticker: str) -> Optional[datetime]:
    """
    Fetch next scheduled earnings date from Yahoo Finance quoteSummary.
    """
    url = f"{YF_QUOTE_SUMMARY_URL.format(ticker=ticker)}?modules=calendarEvents"
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            events = data.get("quoteSummary", {}).get("result", [{}])[0].get("calendarEvents", {})
            earnings_data = events.get("earnings", {}).get("earningsDate", [])
            if earnings_data:
                ts = earnings_data[0].get("raw")
                if ts:
                    return datetime.fromtimestamp(ts, tz=UTC)
    except Exception as e:
        logger.debug(f"[INCOME-SCREENER] No earnings date found for {ticker}: {e}")
    return None


def evaluate_covered_call_candidate(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Evaluate a candidate symbol for Covered Call Income suitability.

    Returns a structured dictionary with:
    - current_price
    - monthly_yield_est (percentage)
    - annualized_yield_est (percentage)
    - selected_strike
    - call_bid, call_ask
    - option_open_interest
    - days_to_earnings
    - earnings_risk_flag
    - income_suitability_score (0 - 100)
    """
    option_data = fetch_yahoo_options(ticker)
    if not option_data:
        return None

    quote = option_data.get("quote", {})
    current_price = quote.get("regularMarketPrice") or quote.get("bid") or 0.0
    if current_price <= 0:
        return None

    options = option_data.get("options", [])
    if not options:
        return None

    calls = options[0].get("calls", [])
    if not calls:
        return None

    # Target an OTM call ~2% to 5% above current market price (Delta ~0.25 - 0.30 proxy)
    target_strike_min = current_price * 1.01
    target_strike_max = current_price * 1.06

    eligible_calls = [
        c for c in calls
        if target_strike_min <= c.get("strike", 0) <= target_strike_max and c.get("bid", 0) > 0
    ]

    selected_call = eligible_calls[0] if eligible_calls else calls[0]
    strike = selected_call.get("strike", current_price)
    bid = selected_call.get("bid", 0.0)
    ask = selected_call.get("ask", 0.0)
    open_interest = selected_call.get("openInterest", 0)
    implied_vol = selected_call.get("impliedVolatility", 0.20)

    # Estimate 30-day monthly yield: bid / current_price
    monthly_yield = (bid / current_price) if current_price > 0 else 0.0
    annualized_yield = monthly_yield * 12.0

    # Earnings check
    next_earnings = fetch_earnings_date(ticker)
    now = datetime.now(UTC)
    days_to_earnings = (next_earnings - now).days if next_earnings else 999
    earnings_risk_flag = (0 <= days_to_earnings <= 30)

    # Calculate 14-day ATR proxy from quote 52-week range or intraday volatility
    fifty_two_high = quote.get("fiftyTwoWeekHigh", current_price)
    fifty_two_low = quote.get("fiftyTwoWeekLow", current_price)
    est_atr_pct = ((fifty_two_high - fifty_two_low) / current_price) / 10.0 if current_price > 0 else 0.015

    # =========================================================================
    # Scoring Formula (0 to 100)
    # =========================================================================
    score = 50.0  # Neutral baseline

    # 1. Premium Yield Contribution (Up to +30 pts)
    # Sweet spot: 1.5% - 3.5% monthly premium
    if 0.015 <= monthly_yield <= 0.040:
        score += 30.0
    elif 0.008 <= monthly_yield < 0.015:
        score += 15.0
    elif monthly_yield > 0.040:
        score += 10.0  # High yield often implies dangerous underlying risk

    # 2. Volatility Stability Contribution (Up to +20 pts)
    # ATR % between 0.8% and 2.5% is ideal for covered calls
    if 0.008 <= est_atr_pct <= 0.025:
        score += 20.0
    elif est_atr_pct > 0.035:
        score -= 20.0  # Too volatile for safe income

    # 3. Liquidity Contribution (Up to +15 pts)
    if open_interest >= 500:
        score += 15.0
    elif open_interest >= 100:
        score += 8.0

    # Bid-Ask spread tightness
    spread = ask - bid
    if spread <= 0.15:
        score += 10.0
    elif spread > 0.35:
        score -= 10.0

    # 4. Earnings Collision Safety (Up to +15 pts / Penalty -25 pts)
    if days_to_earnings > 35:
        score += 15.0
    elif earnings_risk_flag:
        score -= 25.0  # Penalty for holding call across binary earnings event

    final_score = max(5.0, min(99.0, round(score, 1)))

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "strike": strike,
        "call_bid": round(bid, 2),
        "call_ask": round(ask, 2),
        "bid_ask_spread": round(spread, 2),
        "open_interest": open_interest,
        "monthly_yield_est": round(monthly_yield * 100, 2),  # In %
        "annualized_yield_est": round(annualized_yield * 100, 2),  # In %
        "implied_volatility": round(implied_vol * 100, 1),
        "est_atr_pct": round(est_atr_pct * 100, 2),
        "days_to_earnings": days_to_earnings if days_to_earnings != 999 else None,
        "earnings_risk_flag": earnings_risk_flag,
        "income_score": final_score,
        "strategy_track": "INCOME",
    }


def screen_income_candidates(tickers: Optional[List[str]] = None, top_n: int = 5) -> List[Dict[str, Any]]:
    """
    Screen candidate universe for top Covered Call Income leads.

    Returns:
        List of top_n scored candidates sorted by income_score.
    """
    candidates_pool = tickers or INCOME_UNIVERSE
    results = []

    logger.info(f"[INCOME-SCREENER] Screening {len(candidates_pool)} tickers for Covered Call Income...")
    for sym in candidates_pool:
        res = evaluate_covered_call_candidate(sym)
        if res:
            results.append(res)

    results.sort(key=lambda x: x["income_score"], reverse=True)
    top_picks = results[:top_n]
    logger.info(f"[INCOME-SCREENER] Selected top {len(top_picks)} income candidates")
    return top_picks
