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


def test_finnhub_eps_surprise_fallback_in_fundamental_score(monkeypatch):
    """Verify fundamental scoring uses Finnhub EPS surprise when Alpha Vantage returns empty/rate-limited."""
    # get_finnhub_earnings_surprise bails out before calling safe_get unless a
    # FINNHUB_API_KEY is present (env() -> os.getenv), so the mocked safe_get
    # below is only reached when the key is set. Without this the fallback
    # returns None and eps_surprise never populates.
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")

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


def test_fetch_finnhub_news_min_id_and_categories():
    """Verify Finnhub queries minId=0 across both general and merger categories and formats ISO timestamps."""
    from stockpicker.core import fetch_finnhub_news

    now_ts = int(datetime.now(timezone.utc).timestamp())
    old_ts = now_ts - 200000  # Older than 24h

    fake_general = [
        {"id": 101, "datetime": now_ts - 100, "headline": "Oil tankers strike deal in Hormuz", "summary": "Crude surges", "related": "FRO,TNK"},
        {"id": 102, "datetime": old_ts, "headline": "Ancient news headline", "summary": "Old summary", "related": "AAPL"}
    ]
    fake_merger = [
        {"id": 101, "datetime": now_ts - 100, "headline": "Oil tankers strike deal in Hormuz", "summary": "Crude surges", "related": "FRO,TNK"},  # Duplicate
        {"id": 103, "datetime": now_ts - 50, "headline": "CoreWeave acquires AI startup", "summary": "Major cloud merger", "related": "CRWV"}
    ]

    def mock_safe_get(url, headers=None, params=None, timeout=30):
        assert params.get("minId") == 0
        cat = params.get("category")
        if cat == "general":
            return fake_general
        elif cat == "merger":
            return fake_merger
        return []

    with patch("stockpicker.core.env", return_value="fake_finnhub_key"), \
         patch("stockpicker.core.safe_get", side_effect=mock_safe_get):
        since_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        items = fetch_finnhub_news(since_iso)

        # 102 is skipped (old), 101 is deduped across categories -> items should contain 101 and 103
        assert len(items) == 2
        headlines = [it.headline for it in items]
        assert "Oil tankers strike deal in Hormuz" in headlines
        assert "CoreWeave acquires AI startup" in headlines
        # Verify published_at is an ISO formatted string, not raw unix timestamp integer
        for it in items:
            assert "T" in it.published_at
            assert it.published_at.endswith("+00:00") or it.published_at.endswith("Z")


def test_heuristic_rank_avoids_substring_false_positive():
    """Verify words like 'against' or 'claim' do NOT falsely trigger AI/Cloud Infrastructure."""
    from stockpicker.core import heuristic_rank

    records = [
        {
            "headline": "Houthis claim attacks against Red Sea oil facilities",
            "summary": "Crude tanker operations disrupted as Brent surges",
            "engagement": 100,
            "tickers": "FRO"
        }
    ]
    ranked = heuristic_rank(records)
    assert len(ranked) == 1
    # Must correctly identify Energy/Tanker Shipping, NOT AI/Cloud Infrastructure
    assert ranked[0]["industry"] == "Energy/Tanker Shipping"
    # Catalyst words (attacks, surges, oil, tanker) should yield >= 7.5 explosiveness
    assert ranked[0]["explosiveness"] >= 7.5


def test_heuristic_rank_catalyst_detection():
    """Verify high-impact catalysts like FDA approvals and M&A reach >= 7.5 threshold."""
    from stockpicker.core import heuristic_rank

    records = [
        {
            "headline": "FDA approval granted for breakthrough oncology drug therapy",
            "summary": "Biotech firm secures key milestone with commercial rollout",
            "engagement": 200,
            "tickers": "RXRX"
        }
    ]
    ranked = heuristic_rank(records)
    assert len(ranked) == 1
    assert ranked[0]["industry"] == "Biotech/MedTech"
    assert ranked[0]["explosiveness"] >= 7.5
    assert "RXRX" in ranked[0]["impacted_us_tickers"]
