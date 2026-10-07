"""
Alternative Data Client for StockPicker ($0/month Zero-Cost Public APIs).

Fetches quantitative catalyst data from free public government endpoints:
1. USAspending.gov API - Federal Procurement & Government Contract Awards
2. StockWatcher (House & Senate S3 Data) - Congressional Stock Transactions
3. SEC EDGAR Form 4 - Insider Open-Market Transactions
4. Optional QuiverQuant API wrapper (enabled only if QUIVER_API_KEY is configured)
"""

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)
UTC = timezone.utc

USASPENDING_AWARDS_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
HOUSE_STOCK_WATCHER_URL = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_STOCK_WATCHER_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
QUIVER_BASE_URL = "https://api.quiverquant.com/beta"

# Cached transactions to prevent redundant heavy S3 downloads
_congressional_cache: Dict[str, Any] = {"data": None, "timestamp": None}
CONGRESSIONAL_CACHE_TTL = 3600  # 1 hour cache


def fetch_usaspending_contracts(recipient_name: str, days: int = 90) -> List[Dict[str, Any]]:
    """
    Fetch federal procurement contract awards from USAspending.gov (100% Free Public API).

    Args:
        recipient_name: Company name or search keyword (e.g. 'Kratos Defense', 'Palantir')
        days: Historical lookback window in days (default: 90)

    Returns:
        List of awarded contracts with FAIN, agency, amount, and description.
    """
    if not recipient_name:
        return []

    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=days)

    payload = {
        "filters": {
            "time_period": [
                {
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d"),
                }
            ],
            "award_type_codes": ["A", "B", "C", "D"],  # Contracts
            "recipient_search_text": [recipient_name],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Start Date",
            "End Date",
            "Award Amount",
            "Awarding Agency",
            "Description",
        ],
        "limit": 5,
        "page": 1,
    }

    try:
        logger.info(f"[USASPENDING] Querying awards for: {recipient_name}")
        r = requests.post(USASPENDING_AWARDS_URL, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])

        contracts = []
        for item in results:
            contracts.append({
                "award_id": item.get("Award ID", "N/A"),
                "recipient": item.get("Recipient Name", recipient_name),
                "amount": float(item.get("Award Amount") or 0),
                "agency": item.get("Awarding Agency", "Federal Agency"),
                "start_date": item.get("Start Date", ""),
                "description": item.get("Description", "Federal Contract Award"),
            })
        return contracts
    except Exception as e:
        logger.warning(f"[USASPENDING] Failed to query contracts for {recipient_name}: {e}")
        return []


def fetch_congressional_trades(ticker: str, days: int = 60) -> List[Dict[str, Any]]:
    """
    Fetch bipartisan Congressional stock trades from public disclosure feeds (Free Public S3).

    Args:
        ticker: Symbol ticker (e.g. 'KTOS', 'NVDA')
        days: Lookback window in days

    Returns:
        List of matching Congressional purchases within the window.
    """
    now = datetime.now(UTC)

    # Refresh cached transactions if expired
    cached_time = _congressional_cache.get("timestamp")
    if not cached_time or (now - cached_time).total_seconds() > CONGRESSIONAL_CACHE_TTL:
        try:
            logger.info("[STOCKWATCHER] Refreshing congressional disclosures cache...")
            r = requests.get(HOUSE_STOCK_WATCHER_URL, timeout=10)
            if r.status_code == 200:
                _congressional_cache["data"] = r.json()
            else:
                _congressional_cache["data"] = []
            _congressional_cache["timestamp"] = now
        except Exception as e:
            logger.warning(f"[STOCKWATCHER] Failed to fetch House disclosures: {e}")

    transactions = _congressional_cache.get("data") or []
    cutoff_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    matching = []
    ticker_clean = ticker.upper().strip()

    for tx in transactions:
        tx_ticker = str(tx.get("ticker", "")).upper().strip()
        if tx_ticker != ticker_clean:
            continue

        tx_date = str(tx.get("transaction_date", ""))
        if tx_date < cutoff_date:
            continue

        tx_type = str(tx.get("type", "")).lower()
        if "purchase" in tx_type or "buy" in tx_type:
            matching.append({
                "representative": tx.get("representative", "U.S. Representative"),
                "transaction_date": tx_date,
                "disclosure_date": tx.get("disclosure_date", ""),
                "amount": tx.get("amount", "$15,001 - $50,000"),
                "type": "purchase",
                "chamber": "House",
            })

    return matching[:5]


def _parse_congress_bracket_weight(amt_str: str) -> float:
    """Map Congressional disclosure bracket string to relative conviction weight."""
    amt_upper = str(amt_str).upper()
    if "$1,000,001" in amt_upper or "$5,000,000" in amt_upper:
        return 3.0
    if "$500,001" in amt_upper or "$250,001" in amt_upper:
        return 2.5
    if "$100,001" in amt_upper:
        return 2.0
    if "$50,001" in amt_upper:
        return 1.5
    if "$15,001" in amt_upper:
        return 1.0
    if "$1,001" in amt_upper:
        return 0.5
    return 1.0


def compute_congressional_trading_score(
    ticker: str,
    congress_trades: Optional[List[Dict[str, Any]]] = None,
    base_weight: float = 20.0
) -> Dict[str, Any]:
    """
    Compute quantitative conviction score from Congressional STOCK Act disclosures.

    Mathematical Principles:
    1. Exponential Time-Decay: w(t) = exp(-0.033 * days_ago) with ~21 day half-life.
       Disclosures older than 45 days suffer severe alpha decay.
    2. Dollar-Bracket Weighting: Larger purchases ($100k-$250k+) carry greater informational weight.
    3. Trade Clustering: Purchases by multiple distinct lawmakers within the window provide a
       cluster multiplier (1.0 + 0.35 * (unique_members - 1)).

    Args:
        ticker: Symbol ticker (e.g. 'KTOS', 'NVDA')
        congress_trades: Optional pre-fetched list of congressional purchases
        base_weight: Maximum factor score contribution (default: 20.0 pts)

    Returns:
        Dict with:
        - congress_score: Scaled score (0.0 to base_weight)
        - trade_count: Total purchases in window
        - distinct_members: Count of distinct lawmakers
        - latest_trade_days_ago: Days since latest trade
        - reason: Formatted reason string with adjustment (+X.X)
    """
    trades = congress_trades if congress_trades is not None else fetch_congressional_trades(ticker, days=60)
    if not trades or base_weight <= 0:
        return {
            "congress_score": 0.0,
            "trade_count": 0,
            "distinct_members": 0,
            "latest_trade_days_ago": None,
            "reason": "congress_neutral (0.0)",
        }

    now = datetime.now(UTC)
    trade_weights = []
    unique_members = set()
    min_days_ago = 999

    for t in trades:
        member = t.get("representative", "Lawmaker")
        if member:
            unique_members.add(member)

        # Date parsing
        tx_date_str = str(t.get("transaction_date") or t.get("disclosure_date") or "")
        days_ago = 21
        if tx_date_str:
            try:
                tx_dt = datetime.strptime(tx_date_str[:10], "%Y-%m-%d").replace(tzinfo=UTC)
                days_ago = max(0, (now - tx_dt).days)
            except Exception:
                days_ago = 21
        min_days_ago = min(min_days_ago, days_ago)

        # Half-life = 21 days: lambda = ln(2)/21 ~= 0.033
        decay = math.exp(-0.033 * days_ago)
        bracket_w = _parse_congress_bracket_weight(t.get("amount", ""))
        trade_weights.append(bracket_w * decay)

    distinct_count = len(unique_members)
    cluster_mult = 1.0 + 0.35 * max(0, distinct_count - 1)
    raw_signal = sum(trade_weights) * cluster_mult

    # Scaling: A conviction sum of ~3.0 achieves base_weight
    scaled_pts = min(base_weight, raw_signal * (base_weight / 3.0))
    congress_score = round(max(0.0, scaled_pts), 1)

    reason = f"congress_buys={len(trades)} trades ({distinct_count} members) (+{congress_score:.1f})"

    return {
        "congress_score": congress_score,
        "trade_count": len(trades),
        "distinct_members": distinct_count,
        "latest_trade_days_ago": min_days_ago if min_days_ago != 999 else None,
        "reason": reason,
    }


def fetch_quiver_quant_data(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Fetch commercial QuiverQuant data if QUIVER_API_KEY is configured.
    Gracefully returns None if key is absent or API fails.
    """
    api_key = os.getenv("QUIVER_API_KEY")
    if not api_key:
        return None

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        url = f"{QUIVER_BASE_URL}/historical/govcontracts/{ticker}"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return {"gov_contracts": r.json()[:5]}
    except Exception as e:
        logger.warning(f"[QUIVER] Failed to fetch QuiverQuant data for {ticker}: {e}")

    return None


def fetch_alternative_catalysts(ticker: str, company_name: str = "") -> Dict[str, Any]:
    """
    Composite aggregator for alternative data catalysts.

    Returns:
        Dict containing:
        - contracts: List of federal contract awards
        - congress_trades: List of congressional purchases
        - total_contract_value: Sum of recent contracts
        - congress_buy_count: Number of recent congressional purchases
        - has_alternative_catalyst: Boolean flag
    """
    ticker_clean = ticker.upper().strip()
    search_name = company_name or ticker_clean

    contracts = fetch_usaspending_contracts(search_name)
    congress = fetch_congressional_trades(ticker_clean)

    # If QuiverQuant is available, optionally enrich
    quiver_data = fetch_quiver_quant_data(ticker_clean)
    if quiver_data and "gov_contracts" in quiver_data and not contracts:
        for q in quiver_data["gov_contracts"]:
            contracts.append({
                "award_id": q.get("AwardId", "N/A"),
                "recipient": search_name,
                "amount": float(q.get("Amount") or 0),
                "agency": q.get("Agency", "Federal Agency"),
                "start_date": q.get("Date", ""),
                "description": q.get("Description", "QuiverQuant Govt Contract"),
            })

    total_contract_value = sum(c.get("amount", 0) for c in contracts)

    return {
        "ticker": ticker_clean,
        "contracts": contracts,
        "total_contract_value": total_contract_value,
        "contract_count": len(contracts),
        "congress_trades": congress,
        "congress_buy_count": len(congress),
        "has_alternative_catalyst": (len(contracts) > 0 or len(congress) > 0),
    }
