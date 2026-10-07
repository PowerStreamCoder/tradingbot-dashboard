"""
Feed Resilience & Rate Limit Mitigation Test Suite for StockPicker.

Tests:
1. ETF exemption from earnings calendar checks (zero binary collision risk).
2. Finnhub fallback for upcoming earnings calendar dates when Yahoo fails/429s.
3. Finnhub fallback for quarterly EPS surprise when Alpha Vantage hits 25 calls/day limit.
4. Caching mechanisms for options chains and earnings data.
"""

import time
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from stockpicker.income_screener import (
    evaluate_covered_call_candidate,
    fetch_earnings_date,
    fetch_yahoo_options,
    _clear_income_screener_caches,
    KNOWN_ETFS
)
from stockpicker.core import (
    get_alpha_earnings,
    get_finnhub_earnings_surprise,
    compute_fundamental_score,
    _clear_core_caches
)


@pytest.fixture(autouse=True)
def clean_caches():
    _clear_income_screener_caches()
    _clear_core_caches()
    yield
    _clear_income_screener_caches()
    _clear_core_caches()


def test_etf_exempt_from_earnings_calendar():
    """Verify ETFs like SPY are not penalized for having no earnings calendar dates."""
    fake_option = {
        "quote": {"regularMarketPrice": 500.0, "fiftyTwoWeekHigh": 520.0, "fiftyTwoWeekLow": 460.0},
        "options": [{
            "calls": [{
                "strike": 510.0,
                "bid": 8.0,
                "ask": 8.10,
                "openInterest": 10000,
                "impliedVolatility": 0.15
            }]
        }]
    }

    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=fake_option), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=None):
        res = evaluate_covered_call_candidate("SPY")

        assert res is not None
        assert "Earnings Calendar" not in res["missing_sources"]
        assert res["earnings_risk_flag"] is False
        assert res["days_to_earnings"] is None
        # Should not be degraded due to missing earnings
        assert not any("Earnings calendar date unavailable" in w for w in res["degradation_warnings"])


def test_finnhub_earnings_calendar_fallback():
    """Verify non-ETF candidates fall back to Finnhub when Yahoo earnings calendar fails."""
    future_date_str = (datetime.now(timezone.utc) + timedelta(days=50)).strftime("%Y-%m-%d")
    mock_fh_resp = MagicMock()
    mock_fh_resp.status_code = 200
    mock_fh_resp.json.return_value = {
        "earningsCalendar": [{"date": future_date_str, "symbol": "MSFT"}]
    }

    with patch("os.getenv", return_value="fake_finnhub_token"), \
         patch("requests.get", return_value=mock_fh_resp):
        dt = fetch_earnings_date("MSFT")
        assert dt is not None
        assert dt.strftime("%Y-%m-%d") == future_date_str


def test_finnhub_eps_surprise_fallback_in_fundamental_score():
    """Verify fundamental scoring uses Finnhub EPS surprise when Alpha Vantage returns empty/rate-limited."""
    mock_fh_resp = MagicMock()
    mock_fh_resp.status_code = 200
    mock_fh_resp.json.return_value = [
        {"symbol": "AAPL", "actual": 1.50, "estimate": 1.40, "surprisePercent": 7.14}
    ]

    with patch("stockpicker.core.get_alpha_earnings", return_value={}), \
         patch("stockpicker.core.get_companyfacts_quarterly", return_value={"revenue": [{"val": 100}]}), \
         patch("stockpicker.core.get_yahoo_financial_snapshot", return_value={
             "price": {"regularMarketPrice": 180.0, "marketCap": 2800000000000},
             "financialData": {"grossMargins": 0.44, "operatingMargins": 0.30, "debtToEquity": 120.0, "currentRatio": 1.3}
         }), \
         patch("stockpicker.core.safe_get", return_value=[{"symbol": "AAPL", "surprisePercent": 7.14}]), \
         patch("stockpicker.core.get_finnhub_insider_trades", return_value={"insider_score": 0, "reason": "none"}):
        
        fund_score = compute_fundamental_score("AAPL")

        assert fund_score is not None
        assert fund_score["eps_surprise"] == pytest.approx(0.0714, abs=1e-4)
        # Because Finnhub resolved EPS surprise, Alpha Vantage (EPS) must NOT be marked missing
        assert "Alpha Vantage (EPS)" not in fund_score["missing_sources"]


def test_options_cache_prevents_duplicate_calls():
    """Verify options chain is cached for 15 minutes, preventing redundant HTTP requests."""
    fake_option = {
        "quote": {"regularMarketPrice": 100.0},
        "options": [{"calls": [{"strike": 102.0, "bid": 2.0, "ask": 2.1, "openInterest": 500}]}]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"optionChain": {"result": [fake_option]}}

    with patch("requests.get", return_value=mock_resp) as mock_get:
        # First call hits network
        opt1 = fetch_yahoo_options("KO")
        assert opt1 is not None
        assert mock_get.call_count == 1

        # Second call returns from cache without extra HTTP call
        opt2 = fetch_yahoo_options("KO")
        assert opt2 == opt1
        assert mock_get.call_count == 1
