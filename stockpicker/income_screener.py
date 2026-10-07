"""
Covered Call Income Screener for StockPicker (Track 2: INCOME).

Evaluates equities and ETFs for Covered Call income suitability based on:
1. Low realized volatility (ATR/Price between 0.4% and 3.5% per cc_suitability.py)
2. Attractive option premium yield (aiming for 1.8% - 3.5% monthly premium yield on 25-delta calls)
3. Healthy options liquidity (open interest and tight bid-ask spreads)
4. Earnings collision safety (distance to next earnings date > 30 days to avoid assignment shocks)
"""

import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)
UTC = timezone.utc

YF_OPTIONS_URL = "https://query1.finance.yahoo.com/v7/finance/options/{ticker}"
YF_QUOTE_SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Low-volatility, option-rich candidate universe for Covered Call Income scouting
INCOME_UNIVERSE = [
    "SPY", "IWM", "DIA", "XLF", "XLE", "XLV", "XLI", "XLP",
    "JNJ", "PG", "KO", "PFE", "VZ", "T", "BMY", "MRK", "CSCO", "INTC",
    "BAC", "WFC", "USB", "KMB", "DUK", "SO", "NEE", "MCD", "WMT"
]

# Known ETF symbols with no binary single-stock corporate earnings risk
KNOWN_ETFS = {
    "SPY", "IWM", "DIA", "QQQ", "XLF", "XLE", "XLV", "XLI", "XLP",
    "XLU", "XLK", "XLC", "XLB", "XBI", "SMH", "VNQ", "TLT", "EEM",
    "EFA", "VTI", "VOO", "VEA", "VWO", "GLD", "SLV", "GDX"
}

_options_cache: Dict[str, Any] = {}
_earnings_date_cache: Dict[str, Any] = {}
_last_crumb_refresh: float = 0.0


def _clear_income_screener_caches():
    """Clear options and earnings caches for testing."""
    global _last_crumb_refresh
    _options_cache.clear()
    _earnings_date_cache.clear()
    _last_crumb_refresh = 0.0


def fetch_yahoo_options(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Fetch nearest monthly option chain from Yahoo Finance REST API.
    Cached for 15 minutes to reduce HTTP 429 rate limits.

    Returns:
        Dict with current price, expiration dates, and calls chain.
    """
    global _last_crumb_refresh
    now = time.time()
    if ticker in _options_cache:
        cached_time, cached_data = _options_cache[ticker]
        if now - cached_time < 900:  # 15 min TTL
            return cached_data

    from stockpicker.core import _yf_crumb, _yf_cookies, get_yahoo_crumb_and_cookies
    url = YF_OPTIONS_URL.format(ticker=ticker)
    headers = DEFAULT_HEADERS
    params = {}
    crumb, cookies = _yf_crumb, _yf_cookies
    if crumb:
        params["crumb"] = crumb

    try:
        r = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=6)
        if r.status_code in (401, 429) and (now - _last_crumb_refresh > 60):
            _last_crumb_refresh = now
            crumb, cookies = get_yahoo_crumb_and_cookies(force_refresh=True)
            if crumb:
                params["crumb"] = crumb
                r = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=6)

        if r.status_code == 200:
            data = r.json()
            result = data.get("optionChain", {}).get("result", [])
            if result:
                _options_cache[ticker] = (now, result[0])
                return result[0]
    except Exception as e:
        logger.warning(f"[INCOME-SCREENER] Failed to fetch options for {ticker}: {e}")
    return None


def fetch_earnings_date(ticker: str) -> Optional[datetime]:
    """
    Fetch next scheduled earnings date from Yahoo Finance or Finnhub.
    Returns None for ETFs as they do not have corporate earnings.
    """
    global _last_crumb_refresh
    if ticker.upper() in KNOWN_ETFS:
        return None

    now = time.time()
    if ticker in _earnings_date_cache:
        cached_time, cached_dt = _earnings_date_cache[ticker]
        if now - cached_time < 43200:  # 12 hr TTL
            return cached_dt

    from stockpicker.core import _yf_crumb, _yf_cookies, get_yahoo_crumb_and_cookies
    url = f"{YF_QUOTE_SUMMARY_URL.format(ticker=ticker)}?modules=calendarEvents"
    headers = DEFAULT_HEADERS
    params = {"modules": "calendarEvents"}
    crumb, cookies = _yf_crumb, _yf_cookies
    if crumb:
        params["crumb"] = crumb

    try:
        r = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=10)
        if r.status_code in (401, 429) and (now - _last_crumb_refresh > 60):
            _last_crumb_refresh = now
            crumb, cookies = get_yahoo_crumb_and_cookies(force_refresh=True)
            if crumb:
                params["crumb"] = crumb
                r = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=10)

        if r.status_code == 200:
            data = r.json()
            events = data.get("quoteSummary", {}).get("result", [{}])[0].get("calendarEvents", {})
            earnings_data = events.get("earnings", {}).get("earningsDate", [])
            if earnings_data:
                ts = earnings_data[0].get("raw")
                if ts:
                    dt = datetime.fromtimestamp(ts, tz=UTC)
                    _earnings_date_cache[ticker] = (now, dt)
                    return dt
    except Exception as e:
        logger.debug(f"[INCOME-SCREENER] Yahoo earnings date failed for {ticker}: {e}")

    # Fallback to Finnhub earnings calendar
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if finnhub_key:
        try:
            today_str = datetime.now(UTC).strftime('%Y-%m-%d')
            future_str = (datetime.now(UTC) + timedelta(days=120)).strftime('%Y-%m-%d')
            r_fh = requests.get(
                "https://finnhub.io/api/v1/calendar/earnings",
                params={"symbol": ticker, "from": today_str, "to": future_str, "token": finnhub_key},
                timeout=8
            )
            if r_fh.status_code == 200:
                cal = r_fh.json().get("earningsCalendar", [])
                if cal and cal[0].get("date"):
                    dt = datetime.strptime(cal[0]["date"], "%Y-%m-%d").replace(tzinfo=UTC)
                    _earnings_date_cache[ticker] = (now, dt)
                    return dt
        except Exception as e:
            logger.debug(f"[INCOME-SCREENER] Finnhub earnings calendar failed for {ticker}: {e}")

    _earnings_date_cache[ticker] = (now, None)
    return None


def fetch_ticker_quote_metrics(ticker: str) -> Optional[Dict[str, float]]:
    """
    Fetch current price and 52-week high/low metrics via Yahoo chart API (or Finnhub).
    Used as resilient fallback when Yahoo options API is rate-limited (HTTP 429).
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
        r = requests.get(url, headers=headers, timeout=6)
        if r.status_code == 200:
            d = r.json()
            meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
            if price and price > 0:
                return {
                    "current_price": float(price),
                    "fifty_two_high": float(meta.get("fiftyTwoWeekHigh") or (price * 1.15)),
                    "fifty_two_low": float(meta.get("fiftyTwoWeekLow") or (price * 0.85))
                }
    except Exception as e:
        logger.debug(f"[INCOME-SCREENER] Yahoo chart quote failed for {ticker}: {e}")

    # Fallback to Finnhub if available
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if finnhub_key:
        try:
            r = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={finnhub_key}", timeout=6)
            if r.status_code == 200:
                data = r.json()
                price = data.get("c")
                if price and price > 0:
                    high = data.get("h") or (price * 1.05)
                    low = data.get("l") or (price * 0.95)
                    return {
                        "current_price": float(price),
                        "fifty_two_high": float(high * 1.10),
                        "fifty_two_low": float(low * 0.90)
                    }
        except Exception as e:
            logger.debug(f"[INCOME-SCREENER] Finnhub quote failed for {ticker}: {e}")

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
    used_synthetic_options = False
    quote_metrics = None

    if option_data:
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

        monthly_yield = (bid / current_price) if current_price > 0 else 0.0
        annualized_yield = monthly_yield * 12.0
        spread = ask - bid

        fifty_two_high = quote.get("fiftyTwoWeekHigh", current_price)
        fifty_two_low = quote.get("fiftyTwoWeekLow", current_price)
        est_atr_pct = ((fifty_two_high - fifty_two_low) / current_price) / 10.0 if current_price > 0 else 0.015
    else:
        # Live options returned None (e.g. HTTP 429 rate limit) - resilient fallback
        quote_metrics = fetch_ticker_quote_metrics(ticker)
        if not quote_metrics or quote_metrics.get("current_price", 0) <= 0:
            return None
        used_synthetic_options = True

    if used_synthetic_options and quote_metrics:
        current_price = quote_metrics["current_price"]
        fifty_two_high = quote_metrics.get("fifty_two_high", current_price * 1.15)
        fifty_two_low = quote_metrics.get("fifty_two_low", current_price * 0.85)

        est_atr_pct = ((fifty_two_high - fifty_two_low) / current_price) / 10.0 if current_price > 0 else 0.015
        implied_vol = max(0.12, min(0.45, est_atr_pct * 16.0))

        strike = round(current_price * 1.025, 2)
        monthly_yield = max(0.012, min(0.038, est_atr_pct * 1.1))
        annualized_yield = monthly_yield * 12.0

        bid = round(current_price * monthly_yield, 2)
        ask = round(bid * 1.05, 2)
        open_interest = 250
        spread = ask - bid

    # Earnings check (ETFs have no binary earnings event risk)
    is_etf = ticker.upper() in KNOWN_ETFS
    next_earnings = fetch_earnings_date(ticker)
    now = datetime.now(UTC)
    if is_etf:
        days_to_earnings = 999
        earnings_risk_flag = False
    else:
        days_to_earnings = (next_earnings - now).days if next_earnings else 999
        earnings_risk_flag = (0 <= days_to_earnings <= 30)

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
    else:
        score -= 15.0  # Zero or negligible premium yield

    # 2. Volatility Stability Contribution (Up to +20 pts)
    # ATR % between 0.8% and 2.5% is ideal for covered calls
    if 0.008 <= est_atr_pct <= 0.025:
        score += 20.0
    elif est_atr_pct > 0.035 or est_atr_pct < 0.001:
        score -= 15.0  # Too volatile or completely flat

    # 3. Liquidity Contribution (Up to +15 pts)
    if open_interest >= 500:
        score += 15.0
    elif open_interest >= 100:
        score += 8.0
    else:
        score -= 10.0  # Illiquid open interest

    # Bid-Ask spread tightness (supports percentage and absolute spread)
    spread_pct = spread / current_price if current_price > 0 else 0
    if spread <= 0.15 or spread_pct <= 0.002:
        score += 10.0
    elif spread > 0.35 and spread_pct > 0.005:
        score -= 15.0

    # 4. Earnings Collision Safety (Up to +15 pts / Penalty -30 pts & score cap)
    if is_etf or (35 < days_to_earnings < 900):
        score += 15.0
    elif earnings_risk_flag:
        score -= 30.0  # Penalty for holding call across binary earnings event
        score = min(score, 60.0)  # Absolute safety ceiling for earnings collision

    final_score = max(5.0, min(99.0, round(score, 1)))

    missing_sources = []
    degradation_warnings = []

    if used_synthetic_options:
        missing_sources.append("Live Options Chain (Yahoo HTTP 429)")
        degradation_warnings.append("Yahoo options endpoint rate-limited (HTTP 429). Synthetic options yield estimated from price volatility model.")

    if next_earnings is None and not is_etf:
        missing_sources.append("Earnings Calendar")
        degradation_warnings.append("Earnings calendar date unavailable (binary volatility risk unverified)")

    if open_interest < 100:
        degradation_warnings.append(f"Low open interest ({open_interest}) - contract liquidity degraded")

    data_quality = "DEGRADED" if used_synthetic_options else ("PARTIAL" if missing_sources else "FULL")
    is_degraded = bool(missing_sources or degradation_warnings)

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
        "days_to_earnings": days_to_earnings if (days_to_earnings != 999 and not is_etf) else None,
        "earnings_risk_flag": earnings_risk_flag,
        "income_score": final_score,
        "strategy_track": "INCOME",
        "data_quality": data_quality,
        "is_degraded": is_degraded,
        "missing_sources": missing_sources,
        "degradation_warnings": degradation_warnings,
        "used_synthetic_options": used_synthetic_options,
    }


def screen_income_candidates(tickers: Optional[Any] = None, top_n: int = 5) -> List[Dict[str, Any]]:
    """
    Screen candidate universe for top Covered Call Income leads.

    Returns:
        List of top_n scored candidates sorted by income_score.
    """
    if isinstance(tickers, int):
        top_n = tickers
        tickers = None

    candidates_pool = tickers or INCOME_UNIVERSE
    results = []

    from concurrent.futures import ThreadPoolExecutor

    logger.info(f"[INCOME-SCREENER] Screening {len(candidates_pool)} tickers for Covered Call Income...")
    with ThreadPoolExecutor(max_workers=3) as ex:
        eval_results = list(ex.map(evaluate_covered_call_candidate, candidates_pool))

    for res in eval_results:
        if res:
            results.append(res)

    results.sort(key=lambda x: x["income_score"], reverse=True)
    top_picks = results[:top_n]
    logger.info(f"[INCOME-SCREENER] Selected top {len(top_picks)} income candidates")
    return top_picks
