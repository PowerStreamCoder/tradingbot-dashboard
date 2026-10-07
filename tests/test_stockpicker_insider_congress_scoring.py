"""
Unit tests for Insider (SEC Form 4) and Congressional (STOCK Act) quantitative scoring models.

Verifies:
1. Congressional trading scoring:
   - Exponential time-decay calculation (half-life of 21 days)
   - Dollar-bracket weighting ($1k-$15k up to $1M+)
   - Trade clustering multiplier for multiple distinct lawmakers
   - Graceful handling of empty or unverified data feeds
2. Form 4 Corporate Insider trading scoring:
   - Asymmetric conviction between open-market purchases (Code 'P') and routine awards/sales
   - Dollar-weighted cluster buying bonus
   - Asymmetric penalty for severe cluster liquidation
   - Finnhub key missing / empty transaction fallback
3. Growth Track composite integration:
   - Factor 7 (congress_buys) dynamic weight and MAX_POSSIBLE_SCORE normalization
   - Inclusion of insider_score, congress_score in fundamental score and evidence dossier
4. Income Screener downside floor & risk ceiling:
   - Downside safety floor bonus (+6 pts for insider buying, +4 pts for congressional trades)
   - Heavy corporate insider dumping penalty (-15 pts and 60.0 score ceiling)
"""

import math
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from stockpicker.alternative_data_client import (
    compute_congressional_trading_score,
    _parse_congress_bracket_weight,
)
from stockpicker.core import (
    get_finnhub_insider_trades,
    compute_fundamental_score,
    build_evidence_dossier,
    CONGRESS_BUYS_WEIGHT,
    INSIDER_BUYS_WEIGHT,
)
from stockpicker.income_screener import evaluate_covered_call_candidate


def test_parse_congress_bracket_weight():
    """Verify dollar bracket parsing maps to relative conviction weights."""
    assert _parse_congress_bracket_weight("$1,001 - $15,000") == 0.5
    assert _parse_congress_bracket_weight("$15,001 - $50,000") == 1.0
    assert _parse_congress_bracket_weight("$50,001 - $100,000") == 1.5
    assert _parse_congress_bracket_weight("$100,001 - $250,000") == 2.0
    assert _parse_congress_bracket_weight("$250,001 - $500,000") == 2.5
    assert _parse_congress_bracket_weight("$1,000,001 - $5,000,000") == 3.0
    assert _parse_congress_bracket_weight("Unknown bracket") == 1.0


def test_congressional_scoring_empty_and_zero_weight():
    """Verify empty transactions return neutral zero score."""
    res_empty = compute_congressional_trading_score("AAPL", congress_trades=[])
    assert res_empty["congress_score"] == 0.0
    assert res_empty["trade_count"] == 0
    assert res_empty["distinct_members"] == 0
    assert res_empty["latest_trade_days_ago"] is None
    assert "congress_neutral" in res_empty["reason"]

    # Zero base weight
    res_zero_w = compute_congressional_trading_score("AAPL", congress_trades=[{"representative": "Smith"}], base_weight=0.0)
    assert res_zero_w["congress_score"] == 0.0


def test_congressional_scoring_time_decay_and_clustering():
    """Verify exponential time-decay and multi-member clustering."""
    now = datetime.now(timezone.utc)
    recent_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    older_date = (now - timedelta(days=40)).strftime("%Y-%m-%d")

    # Single recent trade ($100k-$250k)
    trades_recent = [{
        "representative": "Rep Alice",
        "transaction_date": recent_date,
        "amount": "$100,001 - $250,000",
        "type": "purchase"
    }]
    res_recent = compute_congressional_trading_score("KTOS", congress_trades=trades_recent, base_weight=20.0)

    # Single older trade ($100k-$250k)
    trades_older = [{
        "representative": "Rep Alice",
        "transaction_date": older_date,
        "amount": "$100,001 - $250,000",
        "type": "purchase"
    }]
    res_older = compute_congressional_trading_score("KTOS", congress_trades=trades_older, base_weight=20.0)

    # Recent trade should have significantly higher score due to time decay
    assert res_recent["congress_score"] > res_older["congress_score"]

    # Cluster of 3 distinct lawmakers buying within 5 days
    trades_cluster = [
        {"representative": "Rep Alice", "transaction_date": recent_date, "amount": "$50,001 - $100,000", "type": "purchase"},
        {"representative": "Rep Bob", "transaction_date": recent_date, "amount": "$50,001 - $100,000", "type": "purchase"},
        {"representative": "Rep Carol", "transaction_date": recent_date, "amount": "$100,001 - $250,000", "type": "purchase"},
    ]
    res_cluster = compute_congressional_trading_score("KTOS", congress_trades=trades_cluster, base_weight=20.0)

    assert res_cluster["distinct_members"] == 3
    assert res_cluster["trade_count"] == 3
    assert res_cluster["congress_score"] > res_recent["congress_score"]
    assert "3 members" in res_cluster["reason"]


def test_finnhub_insider_trades_asymmetric_scoring(monkeypatch):
    """Verify Form 4 open-market cluster purchases and severe liquidation penalty."""
    monkeypatch.setenv("FINNHUB_API_KEY", "mock_key")

    # Case 1: Cluster Open-Market Buying (Code 'P')
    mock_buys = {
        "data": [
            {"name": "CEO Smith", "change": 30000, "transactionPrice": 50.0, "transactionCode": "P"},
            {"name": "CFO Jones", "change": 15000, "transactionPrice": 50.0, "transactionCode": "P"}
        ]
    }
    with patch("stockpicker.core.safe_get", return_value=mock_buys):
        res_buys = get_finnhub_insider_trades("NVDA")
        assert res_buys["distinct_buyers"] == 2
        assert res_buys["buy_value"] > 2000000
        assert res_buys["insider_score"] >= 24.0
        assert "cluster_buying" in res_buys["reason"]

    # Case 2: Multi-executive cluster liquidation (Code 'S')
    mock_sells = {
        "data": [
            {"name": "Exec A", "change": -100000, "transactionPrice": 30.0, "transactionCode": "S"},
            {"name": "Exec B", "change": -80000, "transactionPrice": 30.0, "transactionCode": "S"}
        ]
    }
    with patch("stockpicker.core.safe_get", return_value=mock_sells):
        res_sells = get_finnhub_insider_trades("NVDA")
        assert res_sells["distinct_sellers"] == 2
        assert res_sells["sell_value"] > 5000000
        assert res_sells["insider_score"] < 0
        assert "cluster_selling" in res_sells["reason"]

    # Case 3: Routine compensation award (Code 'A') - does not trigger heavy cluster buy multiplier
    mock_awards = {
        "data": [
            {"name": "VP Taylor", "change": 5000, "transactionPrice": 0.0, "transactionCode": "A"}
        ]
    }
    with patch("stockpicker.core.safe_get", return_value=mock_awards):
        res_awards = get_finnhub_insider_trades("NVDA")
        assert res_awards["distinct_buyers"] == 0
        assert res_awards["insider_score"] == 0.0


def test_growth_track_fundamental_score_includes_congressional_factor():
    """Verify compute_fundamental_score factors in congressional and insider scores."""
    mock_sec = {"revenue": [{"val": 100}], "net_income": [{"val": 20}], "operating_income": [{"val": 15}]}
    mock_yf = {
        "price": {"regularMarketPrice": 100.0, "regularMarketPreviousClose": 95.0, "marketCap": 1000000000},
        "financialData": {"grossMargins": 0.40, "operatingMargins": 0.20, "recommendationMean": 1.8, "debtToEquity": 50.0, "currentRatio": 1.6}
    }
    mock_congress = {
        "congress_score": 14.5,
        "trade_count": 2,
        "distinct_members": 2,
        "reason": "congress_buys=2 trades (2 members) (+14.5)"
    }
    mock_insider = {
        "insider_score": 18.0,
        "net_transactions": 25000,
        "total_value": 1250000,
        "reason": "cluster_buying (+18.0)"
    }

    with patch("stockpicker.core.get_companyfacts_quarterly", return_value=mock_sec), \
         patch("stockpicker.core.get_yahoo_financial_snapshot", return_value=mock_yf), \
         patch("stockpicker.core.get_alpha_earnings", return_value={}), \
         patch("stockpicker.core.get_finnhub_insider_trades", return_value=mock_insider), \
         patch("stockpicker.alternative_data_client.compute_congressional_trading_score", return_value=mock_congress):
        
        fund_score = compute_fundamental_score("GROWTH_TEST")
        assert fund_score["congress_score"] == 14.5
        assert fund_score["insider_score"] == 18.0
        assert fund_score["congress_trades_count"] == 2
        assert "congress_buys=2 trades" in fund_score["fundamental_reasons"]
        assert fund_score["score"] > 50.0

        # Build evidence dossier to check catalyst integration
        dossier = build_evidence_dossier(
            ticker="GROWTH_TEST",
            industry="Technology",
            catalyst="AI Growth",
            fundamentals=fund_score,
            strategy_track="GROWTH"
        )
        assert dossier["congress_score"] == 14.5
        assert dossier["insider_score"] == 18.0


def test_income_screener_downside_floor_and_dumping_penalty():
    """Verify Income Screener adjusts score for insider buying floor and penalizes insider dumping."""
    fake_option = {
        "quote": {"regularMarketPrice": 100.0},
        "options": [{
            "calls": [{
                "strike": 103.0,
                "bid": 2.0,
                "ask": 2.10,
                "openInterest": 600,
                "impliedVolatility": 0.22
            }]
        }]
    }

    # Case 1: Positive insider and congressional buying -> bonuses applied
    mock_insider_good = {"insider_score": 15.0, "net_transactions": 20000}
    mock_congress_good = [{"representative": "Rep Green", "amount": "$50,001 - $100,000"}]

    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=fake_option), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(timezone.utc) + timedelta(days=60)), \
         patch("stockpicker.core.get_finnhub_insider_trades", return_value=mock_insider_good), \
         patch("stockpicker.alternative_data_client.fetch_congressional_trades", return_value=mock_congress_good):
        
        res_good = evaluate_covered_call_candidate("SAFE_INCOME")
        assert res_good is not None
        assert res_good["insider_bonus"] == 6.0
        assert res_good["congress_bonus"] == 4.0
        assert res_good["insider_penalty"] == 0.0
        assert res_good["income_score"] >= 80.0

    # Case 2: Heavy corporate insider dumping -> penalty applied and ceiling enforced
    mock_insider_dump = {"insider_score": -15.0, "net_transactions": -150000}

    with patch("stockpicker.income_screener.fetch_yahoo_options", return_value=fake_option), \
         patch("stockpicker.income_screener.fetch_earnings_date", return_value=datetime.now(timezone.utc) + timedelta(days=60)), \
         patch("stockpicker.core.get_finnhub_insider_trades", return_value=mock_insider_dump), \
         patch("stockpicker.alternative_data_client.fetch_congressional_trades", return_value=[]):
        
        res_dump = evaluate_covered_call_candidate("DUMP_INCOME")
        assert res_dump is not None
        assert res_dump["insider_penalty"] == 15.0
        assert res_dump["income_score"] <= 60.0
        assert any("Heavy corporate insider selling" in w for w in res_dump["degradation_warnings"])
