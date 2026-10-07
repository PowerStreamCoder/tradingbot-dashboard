"""
StockPicker Core Logic
Extracted from standalone app.py - news fetching, ranking, and fundamental scoring

All logic preserved from original:
- News sources: NewsAPI, X/Twitter, Polygon/Benzinga (all optional)
- Ranking: Google Gemini LLM or heuristic fallback (LLM optional)
- Fundamental scoring: SEC EDGAR + Yahoo Finance + Alpha Vantage (AV optional)
- Composite scoring: 60% explosiveness + 40% fundamentals

Key Features:
- All external APIs are optional (graceful degradation)
- Works with zero paid APIs (SEC EDGAR and Yahoo Finance are free)
- Rate limiting for SEC API compliance (10 req/sec)
- Comprehensive error handling with automatic fallbacks
"""

import os
import re
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from collections import deque
import logging

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

UTC = timezone.utc

# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================

# API Endpoints
NEWSAPI_URL = 'https://newsapi.org/v2/everything'
X_RECENT_URL = 'https://api.x.com/2/tweets/search/recent'
POLYGON_BENZINGA_URL = 'https://api.polygon.io/benzinga/v1/news'
FINNHUB_NEWS_URL = 'https://finnhub.io/api/v1/news'
ALPHAVANTAGE_NEWS_URL = 'https://www.alphavantage.co/query'
GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/models'
SEC_TICKER_URL = 'https://www.sec.gov/files/company_tickers.json'
YF_QUOTE_SUMMARY = 'https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}'
ALPHA_VANTAGE_URL = 'https://www.alphavantage.co/query'

# Scoring thresholds - extracted as constants for maintainability
EXPLOSIVENESS_THRESHOLD = 7.5  # Minimum explosiveness score to consider a pick
BASELINE_FUNDAMENTAL_SCORE = 50.0  # Starting score (neutral)

# Legacy weights (kept for backward compatibility)
EXPLOSIVENESS_WEIGHT = 0.60  # 60% weight in composite score
FUNDAMENTAL_WEIGHT = 0.40  # 40% weight in composite score

# 8-Factor Scoring Model Weights
EXPLOSIVENESS_WEIGHT_8F = 5.0      # News explosiveness
RELATIVE_VOLUME_WEIGHT = 4.0       # Trading volume vs average
PRICE_CONF_1D_WEIGHT = 50.0        # 1-day price momentum (highest weight)
FUNDAMENTALS_WEIGHT_8F = 40.0      # Financial metrics
ANALYST_VIEWS_WEIGHT = 40.0        # Analyst recommendations
INSIDER_BUYS_WEIGHT = 30.0         # Insider Form 4 transactions
GROWTH_QUALITY_WEIGHT = 40.0       # Revenue growth + margin quality
PERSISTENCE_WEIGHT = 15.0          # Moving average trend

# Search terms for catalyst detection (from original app.py)
SEARCH_TERMS = [
    'AI compute', 'chip foundry', 'oil prices', 'FDA approval', 'tariffs',
    'earnings surprise', 'merger acquisition', 'guidance raise', 'short report',
    'activist investor', 'cybersecurity breach', 'drug trial', 'robotics',
    'data center', 'cloud demand', 'China export controls', 'rate cut',
    'inflation', 'jobs report', 'Middle East', 'OPEC', 'bank capital',
    'autonomous driving'
]

# Industry to candidate tickers mapping (from original app.py)
# Each industry has 4 small-cap candidates for multibagger potential
SECTOR_TO_CANDIDATES = {
    'AI/Cloud Infrastructure': ['NBIS', 'CORE', 'CRWV', 'SMCI'],
    'Semiconductors/Foundry': ['AMKR', 'AEHR', 'ONTO', 'UCTT'],
    'Humanoid Robotics/Automation': ['RR', 'SYM', 'SERV', 'OUST'],
    'Energy/Tanker Shipping': ['FRO', 'TNK', 'INSW', 'NAT'],
    'Consumer Tech Supply Chain': ['SWKS', 'QRVO', 'OLED', 'COHU'],
    'Biotech/MedTech': ['RXRX', 'CRNX', 'VKTX', 'TMDX'],
    'Cybersecurity': ['S', 'RPD', 'TENB', 'CYBR'],
    'Defense/Aerospace': ['KTOS', 'AVAV', 'RDW', 'PL'],
    'Financials/Fintech': ['SOFI', 'HOOD', 'UPST', 'AFRM'],
}

# Market cap filters for small-cap focus
MULTIBAGGER_MIN_MCAP = float(os.getenv('MULTIBAGGER_MIN_MCAP', 300000000))  # $300M
MULTIBAGGER_MAX_MCAP = float(os.getenv('MULTIBAGGER_MAX_MCAP', 10000000000))  # $10B

# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class NewsItem:
    """
    Represents a single news item from any source.

    Attributes:
        source_type: Source identifier ('newsapi', 'x', 'benzinga')
        source_name: Human-readable source name
        published_at: ISO timestamp of publication
        headline: News headline/title
        summary: Brief summary or full text
        url: Link to original article
        engagement: Engagement metric (likes, retweets, etc.)
        tickers: Comma-separated ticker symbols mentioned
        raw_json: Original JSON response for debugging
    """
    source_type: str
    source_name: str
    published_at: str
    headline: str
    summary: str
    url: str
    engagement: float
    tickers: str
    raw_json: str


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def env(name: str, default=None):
    """
    Get environment variable with optional default.

    Args:
        name: Environment variable name
        default: Default value if not set

    Returns:
        Environment variable value or default
    """
    return os.getenv(name, default)


def now_utc():
    """Get current time in UTC timezone."""
    return datetime.now(UTC)


def iso(dt):
    """
    Convert datetime to ISO 8601 string.

    Args:
        dt: datetime object

    Returns:
        ISO formatted string in UTC (e.g., "2026-08-12T14:30:00+00:00")
    """
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def safe_get(url, headers=None, params=None, timeout=30):
    """
    Make HTTP GET request with error handling.

    Args:
        url: Target URL
        headers: Optional HTTP headers dict
        params: Optional query parameters dict
        timeout: Request timeout in seconds (default 30)

    Returns:
        JSON response as dict

    Raises:
        requests.HTTPError: If request fails
    """
    r = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def sec_headers():
    """
    Generate headers for SEC EDGAR API requests.
    SEC requires a valid User-Agent with contact information.

    Returns:
        Dict with required headers
    """
    ua = env('SEC_USER_AGENT')
    if not ua or 'example.com' in ua:
        ua = 'TradingBotDashboard/1.0 tradingbot-admin@internal.local'
    return {'User-Agent': ua, 'Accept-Encoding': 'gzip, deflate'}


# =============================================================================
# RATE LIMITING (SEC API Compliance)
# =============================================================================

# Track API call timestamps for rate limiting
# SEC allows 10 requests per second per IP
_api_call_times = deque(maxlen=10)


def rate_limited_get(url, headers=None, timeout=30):
    """
    Make rate-limited GET request (SEC API: 10 requests/second).

    Automatically sleeps if we've made 10 requests in the last second
    to comply with SEC EDGAR API rate limits.

    Args:
        url: Target URL
        headers: Optional HTTP headers
        timeout: Request timeout in seconds

    Returns:
        requests.Response object

    Note:
        This implements a sliding window rate limiter for SEC API compliance.
        See: https://www.sec.gov/os/accessing-edgar-data
    """
    # Check if we need to slow down
    if len(_api_call_times) == 10:
        # Calculate time since oldest call
        time_since_oldest = time.time() - _api_call_times[0]
        if time_since_oldest < 1.0:
            # Sleep to stay under 10 req/sec
            sleep_time = 1.0 - time_since_oldest
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)

    # Record this call
    _api_call_times.append(time.time())

    # Make the request
    return requests.get(url, headers=headers or {}, timeout=timeout)


# =============================================================================
# NEWS FETCHING (Optional External APIs)
# =============================================================================

def fetch_newsapi(since_iso: str) -> List[NewsItem]:
    """
    Fetch news from NewsAPI.org (OPTIONAL - requires NEWSAPI_KEY).

    NewsAPI provides curated news articles from various sources.
    Free tier: 100 requests/day, 1-month delay
    Paid tier: $449/month for current news

    Args:
        since_iso: ISO timestamp to fetch news from (e.g., "2026-08-11T00:00:00Z")

    Returns:
        List of NewsItem objects (empty list if API key not set or on error)

    API Docs: https://newsapi.org/docs/endpoints/everything
    """
    api_key = env('NEWSAPI_KEY')
    if not api_key:
        logger.info("NewsAPI key not set, skipping this news source")
        return []

    try:
        # Build search query from first 12 search terms (API limit)
        q = ' OR '.join([f'"{t}"' if ' ' in t else t for t in SEARCH_TERMS[:12]])

        data = safe_get(NEWSAPI_URL, params={
            'q': q,
            'from': since_iso,
            'language': 'en',
            'sortBy': 'popularity',
            'pageSize': 50,  # Max results per request
            'apiKey': api_key
        })

        items = []
        for a in data.get('articles', []):
            items.append(NewsItem(
                source_type='newsapi',
                source_name=(a.get('source') or {}).get('name', 'unknown'),
                published_at=a.get('publishedAt', ''),
                headline=a.get('title') or '',
                summary=a.get('description') or '',
                url=a.get('url') or '',
                engagement=0.0,  # NewsAPI doesn't provide engagement metrics
                tickers='',  # Will be extracted later if needed
                raw_json=json.dumps(a)
            ))

        logger.info(f"Fetched {len(items)} items from NewsAPI")
        return items

    except Exception as e:
        logger.warning(f"Failed to fetch from NewsAPI: {e}")
        return []  # Graceful degradation - return empty list


def fetch_x_recent() -> List[NewsItem]:
    """
    Fetch recent tweets from X/Twitter API (OPTIONAL - requires X_BEARER_TOKEN).

    X API v2 provides access to recent tweets with engagement metrics.
    Free tier: 50 requests/month
    Basic tier: $100/month for 10K requests

    Args:
        None (fetches last ~50 tweets matching criteria)

    Returns:
        List of NewsItem objects (empty list if token not set or on error)

    API Docs: https://developer.twitter.com/en/docs/twitter-api/tweets/search/api-reference
    """
    bearer = env('X_BEARER_TOKEN')
    if not bearer:
        logger.info("X Bearer token not set, skipping Twitter news source")
        return []

    try:
        # Build query for finance-related tweets
        # Excludes retweets to focus on original content
        query = '(stocks OR earnings OR FDA OR merger OR tariffs OR oil OR AI OR semiconductors) lang:en -is:retweet'

        data = safe_get(X_RECENT_URL, headers={'Authorization': f'Bearer {bearer}'}, params={
            'query': query,
            'max_results': 50,
            'tweet.fields': 'created_at,public_metrics,author_id',
            'expansions': 'author_id',
            'user.fields': 'username,name,public_metrics,verified'
        })

        # Build user lookup map
        user_map = {u['id']: u for u in data.get('includes', {}).get('users', [])}
        items = []

        for t in data.get('data', []):
            # Calculate engagement score (weighted: retweets > likes > replies)
            pm = t.get('public_metrics', {})
            engagement = float(
                pm.get('like_count', 0) +
                2 * pm.get('repost_count', 0) +  # Retweets weighted 2x
                0.5 * pm.get('reply_count', 0)    # Replies weighted 0.5x
            )

            # Get author info
            author = user_map.get(t.get('author_id', ''), {})
            source_name = author.get('username') or author.get('name') or 'x'

            # Extract ticker symbols from tweet text ($SYMBOL format)
            text_body = t.get('text', '')
            tickers = ','.join(sorted(set(re.findall(r'\$([A-Z]{1,5})', text_body))))

            items.append(NewsItem(
                source_type='x',
                source_name=source_name,
                published_at=t.get('created_at', ''),
                headline=(text_body[:220]).replace('\n', ' '),  # First 220 chars as headline
                summary=text_body,
                url=f"https://x.com/{source_name}/status/{t.get('id')}",
                engagement=engagement,
                tickers=tickers,
                raw_json=json.dumps(t)
            ))

        logger.info(f"Fetched {len(items)} items from X/Twitter")
        return items

    except Exception as e:
        logger.warning(f"Failed to fetch from X/Twitter: {e}")
        return []  # Graceful degradation


def fetch_polygon_benzinga(since_date: str) -> List[NewsItem]:
    """
    Fetch news from Polygon.io's Benzinga feed (OPTIONAL - requires POLYGON_API_KEY).

    Polygon provides curated financial news from Benzinga.
    Free tier: 5 requests/minute
    Starter tier: $99/month

    Args:
        since_date: Date to fetch news from (format: "YYYY-MM-DD")

    Returns:
        List of NewsItem objects (empty list if API key not set or on error)

    API Docs: https://polygon.io/docs/stocks/get_v1_news
    """
    api_key = env('POLYGON_API_KEY')
    if not api_key:
        logger.info("Polygon API key not set, skipping Benzinga news source")
        return []

    try:
        data = safe_get(POLYGON_BENZINGA_URL, params={
            'published_utc.gte': since_date,
            'limit': 50,
            'apiKey': api_key
        })

        # Handle response format (can be dict with 'results' or direct list)
        results = data.get('results', data if isinstance(data, list) else [])
        items = []

        for a in results:
            # Calculate engagement from channels and tickers mentioned
            engagement = float(
                len(a.get('channels') or []) +
                len(a.get('tickers') or [])
            )

            items.append(NewsItem(
                source_type='benzinga',
                source_name='Benzinga/Polygon',
                published_at=a.get('published_utc', ''),
                headline=a.get('title') or '',
                summary=a.get('summary') or '',
                url=a.get('article_url') or a.get('url') or '',
                engagement=engagement,
                tickers=','.join(a.get('tickers') or []),
                raw_json=json.dumps(a)
            ))

        logger.info(f"Fetched {len(items)} items from Polygon/Benzinga")
        return items

    except Exception as e:
        logger.warning(f"Failed to fetch from Polygon/Benzinga: {e}")
        return []  # Graceful degradation


def fetch_finnhub_news(since_iso: str) -> List[NewsItem]:
    """
    Fetch news from Finnhub (FALLBACK - only used when premium sources unavailable).

    Finnhub provides general market news with free tier (60 calls/min).
    Used as fallback when NewsAPI, Polygon, and Twitter are not configured.

    Args:
        since_iso: ISO timestamp to fetch news from

    Returns:
        List of NewsItem objects (empty list if API key not set or on error)

    API Docs: https://finnhub.io/docs/api/market-news
    """
    api_key = env('FINNHUB_API_KEY')
    if not api_key:
        logger.info("Finnhub API key not set, skipping Finnhub news source")
        return []

    try:
        # Convert ISO timestamp to Unix timestamp (Finnhub uses Unix time)
        from_dt = datetime.fromisoformat(since_iso.replace('Z', '+00:00'))
        from_unix = int(from_dt.timestamp())
        to_unix = int(datetime.now(UTC).timestamp())

        # Fetch general market news
        data = safe_get(FINNHUB_NEWS_URL, params={
            'category': 'general',
            'minId': from_unix,
            'token': api_key
        })

        items = []
        results = data if isinstance(data, list) else []

        for article in results:
            # Filter for catalyst keywords (same as NewsAPI logic)
            headline = article.get('headline', '').lower()
            summary = article.get('summary', '').lower()
            text = f"{headline} {summary}"

            # Check if news mentions catalyst terms
            has_catalyst = any(term.lower() in text for term in SEARCH_TERMS)

            # Calculate engagement from related symbols count
            engagement = float(len(article.get('related', '').split(',')) if article.get('related') else 0)

            items.append(NewsItem(
                source_type='finnhub',
                source_name='Finnhub',
                published_at=str(article.get('datetime', '')),  # Unix timestamp
                headline=article.get('headline', ''),
                summary=article.get('summary', ''),
                url=article.get('url', ''),
                engagement=engagement,
                tickers=article.get('related', ''),  # Comma-separated tickers
                raw_json=json.dumps(article)
            ))

        logger.info(f"Fetched {len(items)} items from Finnhub (fallback source)")
        return items

    except Exception as e:
        logger.warning(f"Failed to fetch from Finnhub: {e}")
        return []  # Graceful degradation


def get_finnhub_insider_trades(ticker: str) -> Dict[str, Any]:
    """
    Get insider trading activity from Finnhub (OPTIONAL - requires FINNHUB_API_KEY).

    Returns cluster buying/selling signals from SEC Form 4 filings.

    Args:
        ticker: Stock symbol (e.g., "AAPL")

    Returns:
        Dict with:
        - insider_score: -30 to +30 (based on INSIDER_BUYS_WEIGHT)
        - net_transactions: Number of buys minus sells (last 30 days)
        - total_value: Dollar value of net transactions
        - reason: Explanation string

    API Docs: https://finnhub.io/docs/api/insider-transactions
    """
    api_key = env('FINNHUB_API_KEY')
    if not api_key:
        return {'insider_score': 0, 'net_transactions': 0, 'total_value': 0, 'reason': 'Finnhub key not set'}

    try:
        # Fetch last 30 days of insider transactions
        from_date = (datetime.now(UTC) - timedelta(days=30)).strftime('%Y-%m-%d')

        data = safe_get('https://finnhub.io/api/v1/stock/insider-transactions', params={
            'symbol': ticker,
            'from': from_date,
            'token': api_key
        })

        transactions = data.get('data', [])

        # Calculate net buying (buys - sells)
        net_shares = 0
        net_value = 0

        for t in transactions:
            change = t.get('change', 0)
            value = t.get('transactionValue', 0) or 0
            transaction_code = t.get('transactionCode', '')

            # P = Purchase, S = Sale, A = Award (treat as positive)
            if transaction_code in ['P', 'A']:
                net_shares += abs(change)
                net_value += abs(value)
            elif transaction_code == 'S':
                net_shares -= abs(change)
                net_value -= abs(value)

        # Score: cluster buying = positive, cluster selling = negative
        if net_shares > 100000:  # Significant buying
            insider_score = INSIDER_BUYS_WEIGHT * 0.8
            reason = f'cluster_buying: {net_shares:,} shares, ${net_value/1e6:.1f}M'
        elif net_shares > 50000:
            insider_score = INSIDER_BUYS_WEIGHT * 0.5
            reason = f'insider_buying: {net_shares:,} shares'
        elif net_shares < -100000:  # Significant selling
            insider_score = -INSIDER_BUYS_WEIGHT * 0.5
            reason = f'cluster_selling: {abs(net_shares):,} shares'
        elif net_shares < -50000:
            insider_score = -INSIDER_BUYS_WEIGHT * 0.3
            reason = f'insider_selling: {abs(net_shares):,} shares'
        else:
            insider_score = 0
            reason = f'neutral: {net_shares:,} net shares'

        return {
            'insider_score': insider_score,
            'net_transactions': net_shares,
            'total_value': net_value,
            'reason': reason
        }

    except Exception as e:
        logger.warning(f"Failed to fetch insider data for {ticker}: {e}")
        return {'insider_score': 0, 'net_transactions': 0, 'total_value': 0, 'reason': f'Error: {str(e)[:50]}'}


def fetch_alphavantage_news(since_iso: str) -> List[NewsItem]:
    """
    Fetch news with sentiment from Alpha Vantage (FREE TIER - preferred over Finnhub).

    Alpha Vantage provides news with sentiment scores and relevance ratings.
    Free tier: 25 requests/day (generous for news scanning)

    Args:
        since_iso: ISO timestamp to fetch news from

    Returns:
        List of NewsItem objects (empty list if API key not set or on error)

    API Docs: https://www.alphavantage.co/documentation/#news-sentiment
    """
    api_key = env('ALPHAVANTAGE_API_KEY')
    if not api_key:
        logger.info("Alpha Vantage API key not set, skipping Alpha Vantage news source")
        return []

    try:
        # Alpha Vantage News Sentiment API
        # By default omitting 'topics' returns all latest market news sorted by recency
        params = {
            'function': 'NEWS_SENTIMENT',
            'limit': 50,  # Max 50 articles per request
            'sort': 'LATEST',
            'apikey': api_key
        }

        data = safe_get(ALPHAVANTAGE_NEWS_URL, params=params)
        if isinstance(data, dict) and ('Information' in data or 'Note' in data):
            logger.warning(f"Alpha Vantage rate limit reached in news: {data.get('Information') or data.get('Note')}")
            return []

        items = []
        feed = data.get('feed', []) if isinstance(data, dict) else []

        # Parse timestamp to filter by recency
        from_dt = datetime.fromisoformat(since_iso.replace('Z', '+00:00'))

        for article in feed:
            # Parse article timestamp
            time_published = article.get('time_published', '')
            try:
                # Format: "20231201T120000" → ISO format
                if len(time_published) >= 15:
                    article_dt = datetime.strptime(time_published[:15], '%Y%m%dT%H%M%S').replace(tzinfo=UTC)

                    # Skip if older than our time window
                    if article_dt < from_dt:
                        continue
            except (ValueError, AttributeError):
                # If parsing fails, include the article anyway
                pass

            # Calculate engagement from sentiment scores
            try:
                sentiment_score = float(article.get('overall_sentiment_score', 0) or 0)
            except (ValueError, TypeError):
                sentiment_score = 0.0

            # Extract tickers mentioned
            ticker_sentiment = article.get('ticker_sentiment', [])
            tickers = ','.join([t.get('ticker', '') for t in ticker_sentiment if t.get('ticker')])

            # Engagement = combination of sentiment magnitude + number of tickers
            engagement = abs(sentiment_score) * 10 + len(ticker_sentiment)

            items.append(NewsItem(
                source_type='alphavantage',
                source_name='Alpha Vantage',
                published_at=time_published,
                headline=article.get('title', ''),
                summary=article.get('summary', ''),
                url=article.get('url', ''),
                engagement=engagement,
                tickers=tickers,
                raw_json=json.dumps(article)
            ))

        logger.info(f"Fetched {len(items)} items from Alpha Vantage News Sentiment")
        return items

    except Exception as e:
        logger.warning(f"Failed to fetch from Alpha Vantage: {e}")
        return []  # Graceful degradation


def fetch_all_news(hours=24) -> List[NewsItem]:
    """
    Fetch news from all available sources with fallback priority.

    Priority order:
    1. Premium sources: NewsAPI, Twitter/X, Polygon/Benzinga
    2. Fallback tier 1: Alpha Vantage (free, 25 calls/day, with sentiment)
    3. Fallback tier 2: Finnhub (free, 60 calls/min, general market news)

    All sources are optional - works with any combination configured.

    Args:
        hours: Number of hours to look back (default 24)

    Returns:
        List of NewsItem objects from all successful sources

    Note:
        If no API keys are configured, returns empty list (not an error).
        The caller should handle empty results gracefully.
    """
    since_dt = now_utc() - timedelta(hours=hours)
    since_iso = iso(since_dt)
    since_date = since_dt.date().isoformat()

    logger.info(f"Fetching news from last {hours} hours (since {since_iso})")

    # Fetch from premium sources first (each returns [] if not configured)
    news_items = []
    news_items.extend(fetch_newsapi(since_iso))
    news_items.extend(fetch_x_recent())
    news_items.extend(fetch_polygon_benzinga(since_date))

    # Use free tier fallbacks only if premium sources returned nothing
    if not news_items:
        logger.info("No results from premium sources - trying free tier alternatives")

        # Try Alpha Vantage first (better quality, sentiment scores)
        news_items.extend(fetch_alphavantage_news(since_iso))

        # If still no results, try Finnhub as last resort
        if not news_items:
            logger.info("No results from Alpha Vantage - using Finnhub as final fallback")
            news_items.extend(fetch_finnhub_news(since_iso))

    logger.info(f"Total fetched: {len(news_items)} news items from all sources")
    return news_items


# =============================================================================
# NEWS RANKING (Google Gemini LLM or Heuristic)
# =============================================================================

def build_llm_prompt(records: List[Dict]) -> str:
    """
    Build prompt for LLM-based news ranking.

    Args:
        records: List of news items as dicts

    Returns:
        Complete prompt string for Gemini API

    Note:
        Only first 40 records sent to avoid token limits (~4000 tokens).
    """
    prompt = (
        'You are a market-news ranking engine. Score each item for EXPLOSIVENESS from 0 to 10. '
        'Use 7.5+ only if clearly extraordinary by news standards. '
        'Return strict JSON list with: headline, explosiveness, industry, direct_ticker, thesis, '
        'impacted_us_tickers, rationale_short. '
        'Industries limited to AI/Cloud Infrastructure, Semiconductors/Foundry, '
        'Humanoid Robotics/Automation, Energy/Tanker Shipping, Consumer Tech Supply Chain, '
        'Biotech/MedTech, Cybersecurity, Defense/Aerospace, Financials/Fintech. '
        f'INPUT={json.dumps(records[:40], ensure_ascii=False)}'
    )
    return prompt


def heuristic_rank(records: List[Dict]) -> List[Dict]:
    """
    Rank news using keyword-based heuristic (FALLBACK when no Gemini key).

    This is a simple keyword matching algorithm that:
    1. Scores each news item based on keyword matches
    2. Maps to most relevant industry
    3. Calculates explosiveness from engagement and keyword density

    Args:
        records: List of news items as dicts (headline, summary, engagement)

    Returns:
        List of ranked items with explosiveness scores

    Note:
        Scores typically range 5.0-9.5 (lower than LLM which can be more nuanced).
        This is acceptable for basic operation but LLM gives better results.
    """
    # Industry keyword mappings
    keywords = {
        'AI/Cloud Infrastructure': ['ai', 'data center', 'cloud', 'gpu'],
        'Semiconductors/Foundry': ['chip', 'semiconductor', 'foundry', 'intel', 'tsmc'],
        'Humanoid Robotics/Automation': ['robot', 'humanoid', 'automation'],
        'Energy/Tanker Shipping': ['oil', 'brent', 'hormuz', 'opec', 'tanker'],
        'Consumer Tech Supply Chain': ['apple', 'iphone', 'consumer tech'],
        'Biotech/MedTech': ['fda', 'trial', 'drug', 'biotech'],
        'Cybersecurity': ['cyber', 'breach', 'ransomware'],
        'Defense/Aerospace': ['defense', 'missile', 'drone', 'satellite'],
        'Financials/Fintech': ['bank', 'payment', 'fintech', 'credit']
    }

    out = []
    for rec in records:
        # Combine headline and summary for keyword matching
        text = (str(rec.get('headline', '')) + ' ' + str(rec.get('summary', ''))).lower()

        # Find best matching industry
        industry = 'Financials/Fintech'  # Default
        best_match_score = 0

        for ind, kws in keywords.items():
            score = sum(k in text for k in kws)
            if score > best_match_score:
                best_match_score, industry = score, ind

        # Calculate explosiveness: baseline 5.0 + engagement + keyword density (score >= 2 reaches 7.6+)
        explosive = 5.0 + min(4.5, rec.get('engagement', 0) / 2000 + best_match_score * 1.3)

        out.append({
            'headline': rec.get('headline'),
            'explosiveness': round(explosive, 2),
            'industry': industry,
            'direct_ticker': (rec.get('tickers', '').split(',')[0] if rec.get('tickers') else ''),
            'thesis': 'Heuristic sector impact classification',
            'impacted_us_tickers': SECTOR_TO_CANDIDATES.get(industry, [])[:4],
            'rationale_short': f'Derived from keyword match (score: {best_match_score}) and engagement proxy'
        })

    logger.info(f"Heuristic ranking completed: {len(out)} items scored")
    return out


def call_llm_rank(records: List[Dict]) -> List[Dict]:
    """
    Rank news using Google Gemini LLM (OPTIONAL - requires GEMINI_API_KEY).

    Uses gemini-2.5-flash by default (high speed, cost-effective or free tier).
    Falls back to heuristic ranking if:
    - GEMINI_API_KEY not set
    - API call fails
    - Response format invalid

    Args:
        records: List of news items as dicts

    Returns:
        List of ranked items with LLM-generated explosiveness scores

    Note:
        LLM provides more nuanced scoring (can distinguish 7.2 vs 8.5)
        and better industry categorization than heuristic approach.
    """
    api_key = env('GEMINI_API_KEY')
    if not api_key:
        logger.info("Gemini key not set, using heuristic ranking as fallback")
        return heuristic_rank(records)

    try:
        model = env('GEMINI_MODEL', 'gemini-2.5-flash')
        url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            'contents': [
                {
                    'parts': [
                        {'text': build_llm_prompt(records)}
                    ]
                }
            ],
            'generationConfig': {
                'temperature': 0.1,
                'responseMimeType': 'application/json'
            }
        }

        logger.info(f"Calling Gemini API ({model}) to rank {len(records)} news items...")
        r = requests.post(url, headers=headers, json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()

        candidates = data.get('candidates', [])
        if not candidates:
            logger.warning("Gemini returned no candidates, falling back to heuristic")
            return heuristic_rank(records)

        text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')

        try:
            ranked = json.loads(text)
        except Exception:
            m = re.search(r'(\[.*\])', text, re.S)
            if not m:
                logger.warning("Gemini did not return JSON list, falling back to heuristic")
                return heuristic_rank(records)
            ranked = json.loads(m.group(1))

        if not isinstance(ranked, list):
            logger.warning("Gemini response is not a list, falling back to heuristic")
            return heuristic_rank(records)

        logger.info(f"Gemini ranked {len(ranked)} items successfully")
        return ranked

    except Exception as e:
        logger.warning(f"Gemini ranking failed: {e}, falling back to heuristic")
        return heuristic_rank(records)


def generate_fundamentals_picks(top_n=20) -> List[Dict]:
    """
    Generate stock picks based purely on fundamentals when no news is available.

    Strategy:
    - Score all tickers from SECTOR_TO_CANDIDATES
    - Rank by fundamental_score (revenue growth, profitability, margins)
    - Return top N candidates with highest scores

    Returns:
        List of dicts matching rank_news() output format for compatibility
    """
    candidates = []

    # Flatten all sector tickers
    all_tickers = []
    for sector, tickers in SECTOR_TO_CANDIDATES.items():
        for ticker in tickers:
            all_tickers.append({'ticker': ticker, 'sector': sector})

    logger.info(f"Scoring {len(all_tickers)} tickers based on fundamentals only...")

    scores_summary = []  # Track scores for debugging

    from concurrent.futures import ThreadPoolExecutor

    def score_ticker_item(item):
        t = item['ticker']
        sec = item['sector']
        try:
            fund_score = compute_fundamental_score(t)
            if fund_score and fund_score.get('score', 0) >= 10:
                fund_missing = sorted(list(set(fund_score.get('missing_sources', []) + ['News Catalysts'])))
                fund_warnings = fund_score.get('degradation_warnings', []) + ['News catalyst feeds returned no explosive news (explosiveness 0.0)']
                return {
                    'headline': f"Strong fundamentals in {sec}",
                    'explosiveness': 0.0,
                    'industry': sec,
                    'direct_ticker': t,
                    'impacted_us_tickers': [t],
                    'thesis': f"{t} shows solid financial metrics: {fund_score.get('fundamental_reasons', 'Good fundamentals')}",
                    'rationale_short': 'No news available - selected by fundamental analysis',
                    'news_source': 'fundamentals_only',
                    'published_at': datetime.now(UTC).isoformat(),
                    'fundamental_score': fund_score['score'],
                    'ticker': t,
                    'data_quality': 'PARTIAL',
                    'is_degraded': True,
                    'missing_sources': fund_missing,
                    'degradation_warnings': fund_warnings
                }
        except Exception as e:
            logger.warning(f"Failed to score {t}: {e}")
        return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(score_ticker_item, all_tickers))

    for cand in results:
        if cand:
            candidates.append(cand)
            scores_summary.append(f"{cand['ticker']}:{cand['fundamental_score']:.0f}")

    # Sort by fundamental score
    candidates.sort(key=lambda x: x['fundamental_score'], reverse=True)

    logger.info(f"Generated {len(candidates)} candidates from fundamentals, returning top {top_n}")
    if scores_summary:
        logger.info(f"Sample scores: {', '.join(scores_summary[:10])}")
    return candidates[:top_n]


def rank_news(news_items: List[NewsItem]) -> List[Dict]:
    """
    Rank news by explosiveness using LLM or heuristic fallback.

    This is the main entry point for news ranking.
    Automatically uses Google Gemini LLM if API key available, else heuristic.

    Args:
        news_items: List of NewsItem objects from news sources

    Returns:
        List of dicts with ranked news and metadata:
        - headline: News headline
        - explosiveness: Score 0-10 (use 7.5+ for picks)
        - industry: Mapped industry sector
        - direct_ticker: Ticker mentioned in news (if any)
        - impacted_us_tickers: Candidate tickers for this industry
        - thesis: Brief thesis for the pick
        - rationale_short: Why this score was assigned

    Note:
        Returns fundamentals-only picks if no news items provided.
    """
    if not news_items:
        logger.warning("No news available - generating picks from fundamentals only")
        return generate_fundamentals_picks(top_n=20)

    # Convert NewsItem objects to dicts for ranking
    records = []
    for item in news_items:
        records.append({
            'headline': item.headline,
            'summary': item.summary,
            'url': item.url,
            'engagement': item.engagement,
            'tickers': item.tickers,
            'source_type': item.source_type,
            'source_name': item.source_name
        })

    # Call LLM (which falls back to heuristic if needed)
    return call_llm_rank(records)


# =============================================================================
# FUNDAMENTAL SCORING (SEC EDGAR + Yahoo Finance + Alpha Vantage)
# =============================================================================

# In-memory cache for SEC ticker map (reused across calls)
_fundamental_cache = {}


def get_sec_ticker_map() -> Dict[str, str]:
    """
    Get SEC CIK to ticker mapping (cached).

    CIK (Central Index Key) is SEC's identifier for companies.
    This mapping allows us to look up financial data by ticker symbol.

    Returns:
        Dict mapping ticker (str) -> CIK (str, 10 digits zero-padded)

    Note:
        Cached after first call to avoid repeated downloads.
        See: https://www.sec.gov/files/company_tickers.json
    """
    if 'sec_ticker_map' in _fundamental_cache:
        return _fundamental_cache['sec_ticker_map']

    try:
        r = rate_limited_get(SEC_TICKER_URL, headers=sec_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()

        # Build ticker -> CIK mapping
        mp = {str(v['ticker']).upper(): str(v['cik_str']).zfill(10) for v in data.values()}
        _fundamental_cache['sec_ticker_map'] = mp

        logger.info(f"Loaded SEC ticker map: {len(mp)} companies")
        return mp

    except Exception as e:
        logger.warning(f"Failed to fetch SEC ticker map: {e}")
        _fundamental_cache['sec_ticker_map'] = {}
        return {}


def get_companyfacts_quarterly(ticker: str) -> Dict[str, List]:
    """
    Get quarterly financial facts from SEC EDGAR (FREE - no API key required).

    Fetches financial data from company's XBRL filings:
    - Revenue (last 8 quarters)
    - Net income (last 8 quarters)
    - Operating income (last 8 quarters)
    - EPS (last 8 quarters)
    - Assets (last 8 quarters)
    - Cash (last 8 quarters)

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dict with keys: revenue, net_income, operating_income, eps, assets, cash
        Each value is a list of quarterly data points (most recent first)

    Note:
        Rate limited to 10 requests/second per SEC API rules.
        Returns empty dict if ticker not found or on error.

    API Docs: https://www.sec.gov/edgar/sec-api-documentation
    """
    mp = get_sec_ticker_map()
    cik = mp.get(ticker.upper())
    if not cik:
        logger.debug(f"Ticker {ticker} not found in SEC CIK map")
        return {}

    try:
        url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
        r = rate_limited_get(url, headers=sec_headers(), timeout=30)

        if r.status_code != 200:
            return {}

        facts = r.json().get('facts', {}).get('us-gaap', {})

        # Map XBRL concepts to our keys (multiple concepts per metric for compatibility)
        concept_map = {
            'revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet'],
            'net_income': ['NetIncomeLoss'],
            'operating_income': ['OperatingIncomeLoss'],
            'eps': ['EarningsPerShareDiluted', 'EarningsPerShareBasicAndDiluted'],
            'assets': ['Assets'],
            'cash': ['CashAndCashEquivalentsAtCarryingValue'],
        }

        out = {}
        for name, concepts in concept_map.items():
            vals = []

            # Try each concept variant (companies use different XBRL tags)
            for c in concepts:
                node = facts.get(c, {})
                units = node.get('units', {})

                # Extract all values from all unit types (USD, shares, etc.)
                for unit_name, arr in units.items():
                    for x in arr:
                        # Only include official filings (not amendments unless necessary)
                        if x.get('form') in ('10-Q', '10-Q/A', '10-K', '20-F', '6-K'):
                            vals.append(x)

            # Sort by end date (most recent first), keep last 8 quarters
            vals.sort(key=lambda x: x.get('end', ''), reverse=True)
            out[name] = vals[:8]

        logger.debug(f"Fetched SEC data for {ticker}: {len(out)} metrics")
        return out

    except Exception as e:
        logger.warning(f"Failed to get SEC data for {ticker}: {e}")
        return {}


# Cached Yahoo Finance crumb & cookies for authenticated REST endpoints
_yf_crumb = None
_yf_cookies = None
_yf_crumb_time = None


def get_yahoo_crumb_and_cookies(force_refresh: bool = False):
    """
    Get or refresh Yahoo Finance crumb and cookies via requests.get.
    """
    global _yf_crumb, _yf_cookies, _yf_crumb_time
    now = time.time()
    if not force_refresh and _yf_crumb is not None and _yf_cookies is not None and (now - (_yf_crumb_time or 0) < 3600):
        return _yf_crumb, _yf_cookies

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        r_fc = requests.get('https://fc.yahoo.com', headers=headers, timeout=10)
        cookies = r_fc.cookies
        r_crumb = requests.get('https://query2.finance.yahoo.com/v1/test/getcrumb', headers=headers, cookies=cookies, timeout=10)
        if r_crumb.status_code == 200 and r_crumb.text:
            _yf_crumb = r_crumb.text.strip()
            _yf_cookies = cookies
            _yf_crumb_time = now
            return _yf_crumb, _yf_cookies
    except Exception as e:
        logger.debug(f"Failed to acquire Yahoo Finance crumb: {e}")

    return None, None


def get_yahoo_financial_snapshot(ticker: str) -> Dict:
    """
    Get financial snapshot from Yahoo Finance (FREE - no API key required).

    Yahoo Finance provides real-time financial metrics via unofficial API.
    Data includes:
    - Market cap
    - Debt to equity ratio
    - Current ratio
    - Gross margins
    - Operating margins
    - Analyst recommendations

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dict with Yahoo Finance data (nested structure)
        Empty dict if ticker not found or on error
    """
    try:
        modules = 'financialData,defaultKeyStatistics,price,summaryDetail,calendarEvents'
        url = YF_QUOTE_SUMMARY.format(ticker=ticker)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        params = {'modules': modules}

        crumb, cookies = _yf_crumb, _yf_cookies
        if crumb:
            params['crumb'] = crumb

        r = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=15)

        if r.status_code in (401, 429):
            crumb, cookies = get_yahoo_crumb_and_cookies(force_refresh=True)
            if crumb:
                params['crumb'] = crumb
                r = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=15)

        if r.status_code != 200:
            return {}

        result = ((r.json().get('quoteSummary') or {}).get('result') or [{}])[0]
        logger.debug(f"Fetched Yahoo Finance data for {ticker}")
        return result

    except Exception as e:
        logger.warning(f"Failed to get Yahoo data for {ticker}: {e}")
        return {}


_av_earnings_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_finnhub_earnings_cache: Dict[str, Tuple[float, Optional[float]]] = {}


def _clear_core_caches():
    """Clear in-memory caches for testing."""
    global _fundamental_cache, _fundamental_scores_cache, _av_earnings_cache, _finnhub_earnings_cache
    _fundamental_cache.clear()
    _fundamental_scores_cache.clear()
    _av_earnings_cache.clear()
    _finnhub_earnings_cache.clear()


def get_alpha_earnings(ticker: str) -> Dict:
    """
    Get earnings data from Alpha Vantage (OPTIONAL - requires ALPHAVANTAGE_API_KEY).
    Cached for 24 hours to prevent exceeding the 25 calls/day free tier quota.

    Alpha Vantage provides:
    - Quarterly EPS actual vs. expected
    - Earnings surprise percentage
    - Historical earnings trends

    Free tier: 25 requests/day
    Premium: $49/month

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dict with earnings data (empty if API key not set or on error)

    API Docs: https://www.alphavantage.co/documentation/
    """
    now = time.time()
    if ticker in _av_earnings_cache:
        cached_time, cached_data = _av_earnings_cache[ticker]
        if now - cached_time < 86400:  # 24h TTL
            return cached_data

    key = env('ALPHAVANTAGE_API_KEY')
    if not key:
        logger.debug("Alpha Vantage API key not set, skipping earnings data")
        return {}

    try:
        r = requests.get(ALPHA_VANTAGE_URL, params={
            'function': 'EARNINGS',
            'symbol': ticker,
            'apikey': key
        }, timeout=15)

        if r.status_code != 200:
            return {}

        data = r.json()
        if isinstance(data, dict) and ('Information' in data or 'Note' in data):
            logger.warning(f"Alpha Vantage rate limit reached for {ticker}: {data.get('Information') or data.get('Note')}")
            # Cache empty failure for 1 hour so we don't spam a rate-limited endpoint
            _av_earnings_cache[ticker] = (now, {})
            return {}

        logger.debug(f"Fetched Alpha Vantage earnings data for {ticker}")
        if isinstance(data, dict) and 'quarterlyEarnings' in data:
            _av_earnings_cache[ticker] = (now, data)
        return data

    except Exception as e:
        logger.warning(f"Failed to get Alpha Vantage data for {ticker}: {e}")
        return {}


def get_finnhub_earnings_surprise(ticker: str) -> Optional[float]:
    """
    Fetch quarterly EPS surprise from Finnhub (60 requests/minute free tier).
    Provides resilient fallback when Alpha Vantage is rate-limited or hits 25 calls/day limit.

    Returns:
        float: EPS surprise percentage as decimal (e.g. 0.045 for +4.5%), or None
    """
    now = time.time()
    if ticker in _finnhub_earnings_cache:
        cached_time, val = _finnhub_earnings_cache[ticker]
        if now - cached_time < 86400:
            return val

    key = env('FINNHUB_API_KEY')
    if not key:
        return None

    try:
        data = safe_get('https://finnhub.io/api/v1/stock/earnings', params={
            'symbol': ticker,
            'token': key
        }, timeout=10)
        if isinstance(data, list) and len(data) > 0:
            surprise_pct = data[0].get('surprisePercent')
            if surprise_pct not in (None, 'None'):
                val = float(surprise_pct) / 100.0
                _finnhub_earnings_cache[ticker] = (now, val)
                return val
    except Exception as e:
        logger.debug(f"Finnhub earnings surprise query failed for {ticker}: {e}")

    _finnhub_earnings_cache[ticker] = (now, None)
    return None


def latest_numeric(obj, path_list):
    """
    Extract numeric value from nested dict following path.

    Helper function to navigate Yahoo Finance's nested response structure.

    Args:
        obj: Dict to navigate
        path_list: List of keys to follow (e.g., ['price', 'marketCap'])

    Returns:
        Numeric value or None if path not found

    Example:
        >>> data = {'price': {'marketCap': {'raw': 3000000000}}}
        >>> latest_numeric(data, ['price', 'marketCap'])
        3000000000
    """
    cur = obj
    for k in path_list:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]

    # Yahoo Finance returns {'raw': value, 'fmt': formatted_string}
    if isinstance(cur, dict) and 'raw' in cur:
        cur = cur['raw']
    if isinstance(cur, (int, float)):
        return float(cur)
    return None


def safe_yoy_from_quarters(items):
    """
    Calculate Year-over-Year growth from quarterly data.

    Compares Q0 (most recent) to Q4 (same quarter last year).

    Args:
        items: List of quarterly data points (sorted newest first)
        Each item has 'val' and 'end' fields

    Returns:
        YoY growth rate as decimal (e.g., 0.15 = 15% growth)
        None if insufficient data

    Example:
        >>> quarters = [{'val': 100}, {'val': 90}, {'val': 85}, {'val': 80}, {'val': 87}]
        >>> safe_yoy_from_quarters(quarters)
        0.1494  # (100/87 - 1) = 14.94% growth
    """
    if len(items) >= 5:
        try:
            a = float(items[0]['val'])  # Most recent quarter
            b = float(items[4]['val'])  # Same quarter last year
            if b != 0:
                return (a / b) - 1.0
        except (ValueError, KeyError, TypeError):
            return None
    return None


def latest_val(items):
    """
    Get latest value from list of quarterly data points.

    Args:
        items: List of data points with 'val' field

    Returns:
        Float value or None if empty/invalid
    """
    try:
        return float(items[0]['val']) if items else None
    except (ValueError, KeyError, TypeError, IndexError):
        return None


# Cache for compute_fundamental_score within run
_fundamental_scores_cache: Dict[Any, Dict[str, Any]] = {}


def compute_fundamental_score(ticker: str, weight_adjustments: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Compute fundamental score (0-100) for a ticker.

    This is the core fundamental analysis function. It:
    1. Fetches data from SEC EDGAR (free)
    2. Fetches data from Yahoo Finance (free)
    3. Fetches earnings from Alpha Vantage (optional)
    4. Calculates composite score from multiple metrics

    Scoring Algorithm:
    - Baseline: 50 (neutral)
    - Revenue YoY: +20 to -15 (growth is very important)
    - Net income positive: +8 or -8
    - Operating income positive: +6 or -6
    - Gross margin: +8 to -5 (vs 20% baseline)
    - Operating margin: +10 to -6
    - Debt/Equity < 100: +4, > 250: -4
    - Current ratio >= 1.2: +4, else -4
    - EPS surprise: +10 to -8
    - Analyst recommendation <= 2.2: +3, >= 3.5: -3

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dict with:
        - score: 0-100 (clamped)
        - revenue_yoy: Revenue growth rate
        - net_income: Latest quarterly net income
        - operating_income: Latest quarterly operating income
        - cash: Latest cash balance
        - market_cap: Current market capitalization
        - debt_to_equity: Debt to equity ratio
        - current_ratio: Current assets / current liabilities
        - gross_margin: Gross profit margin
        - operating_margin: Operating profit margin
        - eps_surprise: Latest earnings surprise percentage
        - recommendation_mean: Analyst recommendation (1=buy, 5=sell)
        - fundamental_reasons: String explaining score adjustments

    Note:
        Each metric is optional - missing metrics don't affect baseline score.
        This ensures we can still score companies with incomplete data.
    """
    cache_key = (ticker.upper(), tuple(sorted((weight_adjustments or {}).items())))
    if cache_key in _fundamental_scores_cache:
        return dict(_fundamental_scores_cache[cache_key])

    # Fetch data from all sources
    sec = get_companyfacts_quarterly(ticker)
    yf = get_yahoo_financial_snapshot(ticker)
    av = get_alpha_earnings(ticker)

    # Extract metrics from SEC EDGAR data
    revenue_yoy = safe_yoy_from_quarters(sec.get('revenue', []))
    net_income = latest_val(sec.get('net_income', []))
    op_income = latest_val(sec.get('operating_income', []))
    cash = latest_val(sec.get('cash', []))

    # Extract metrics from Yahoo Finance
    market_cap = latest_numeric(yf, ['price', 'marketCap'])
    debt_to_equity = latest_numeric(yf, ['financialData', 'debtToEquity'])
    current_ratio = latest_numeric(yf, ['financialData', 'currentRatio'])
    gross_margin = latest_numeric(yf, ['financialData', 'grossMargins'])
    operating_margin = latest_numeric(yf, ['financialData', 'operatingMargins'])
    recommendation = latest_numeric(yf, ['financialData', 'recommendationMean'])

    # New fields for 8-factor model
    current_price = latest_numeric(yf, ['price', 'regularMarketPrice'])
    previous_close = latest_numeric(yf, ['price', 'regularMarketPreviousClose'])
    current_volume = latest_numeric(yf, ['price', 'regularMarketVolume'])
    average_volume = latest_numeric(yf, ['price', 'averageVolume'])
    ma_50 = latest_numeric(yf, ['summaryDetail', 'fiftyDayAverage'])
    ma_200 = latest_numeric(yf, ['summaryDetail', 'twoHundredDayAverage'])

    # Extract EPS surprise from Alpha Vantage (optional)
    q_eps = ((av.get('quarterlyEarnings') or [])[:4]) if isinstance(av, dict) else []
    eps_surprise = None
    if q_eps:
        try:
            surprise_pct = q_eps[0].get('surprisePercentage')
            if surprise_pct not in (None, 'None'):
                eps_surprise = float(surprise_pct) / 100.0
        except (ValueError, TypeError, KeyError):
            eps_surprise = None

    # Fallback 1: Finnhub earnings surprise (resilient against Alpha Vantage 25/day limit)
    if eps_surprise is None:
        eps_surprise = get_finnhub_earnings_surprise(ticker)

    # Fallback 2: Yahoo Finance earnings history if available
    if eps_surprise is None and yf:
        history = (yf.get('earningsHistory') or {}).get('history') or []
        if history and isinstance(history, list):
            try:
                latest_q = history[-1]
                surp = latest_q.get('surprisePercent')
                if isinstance(surp, dict):
                    surp = surp.get('raw')
                if surp is not None:
                    eps_surprise = float(surp)
            except Exception:
                pass

    # Calculate composite score
    score = BASELINE_FUNDAMENTAL_SCORE  # Start at 50 (neutral)
    reasons = []

    # Revenue growth (most important: +20 to -15)
    if revenue_yoy is not None:
        adjustment = max(-15, min(20, revenue_yoy * 40))
        score += adjustment
        reasons.append(f'revenue_yoy={revenue_yoy:.2%} ({adjustment:+.1f})')

    # Net income positive/negative (+8 or -8)
    if net_income is not None:
        if net_income > 0:
            score += 8
            reasons.append('net_income_positive (+8)')
        else:
            score -= 8
            reasons.append('net_income_negative (-8)')

    # Operating income positive/negative (+6 or -6)
    if op_income is not None:
        if op_income > 0:
            score += 6
            reasons.append('operating_income_positive (+6)')
        else:
            score -= 6
            reasons.append('operating_income_negative (-6)')

    # Gross margin (vs 20% baseline: +8 to -5)
    if gross_margin is not None:
        adjustment = max(-5, min(8, (gross_margin - 0.20) * 20))
        score += adjustment
        reasons.append(f'gross_margin={gross_margin:.1%} ({adjustment:+.1f})')

    # Operating margin (+10 to -6)
    if operating_margin is not None:
        adjustment = max(-6, min(10, operating_margin * 20))
        score += adjustment
        reasons.append(f'operating_margin={operating_margin:.1%} ({adjustment:+.1f})')

    # Debt to equity (<100 good, >250 bad)
    if debt_to_equity is not None:
        if debt_to_equity < 100:
            score += 4
            reasons.append('debt_to_equity<100 (+4)')
        elif debt_to_equity > 250:
            score -= 4
            reasons.append('debt_to_equity>250 (-4)')
        else:
            reasons.append(f'debt_to_equity={debt_to_equity:.0f} (0)')

    # Current ratio (>=1.2 good, <1.2 bad)
    if current_ratio is not None:
        if current_ratio >= 1.2:
            score += 4
            reasons.append(f'current_ratio={current_ratio:.2f} (+4)')
        else:
            score -= 4
            reasons.append(f'current_ratio={current_ratio:.2f} (-4)')

    # EPS surprise (+10 to -8)
    if eps_surprise is not None:
        adjustment = max(-8, min(10, eps_surprise * 25))
        score += adjustment
        reasons.append(f'eps_surprise={eps_surprise:.2%} ({adjustment:+.1f})')

    # ========================================================================
    # 8-FACTOR MODEL: Additional Scoring Factors
    # ========================================================================

    # Dynamic factor weight adaptations from Closed-Loop Feedback Engine (Phase 7)
    adj = weight_adjustments or {}
    w_rel_vol = RELATIVE_VOLUME_WEIGHT * adj.get("relative_volume", 1.0)
    w_price_1d = PRICE_CONF_1D_WEIGHT * adj.get("price_momentum_1d", 1.0)
    w_analyst = ANALYST_VIEWS_WEIGHT * adj.get("analyst_views", 1.0)
    w_growth = GROWTH_QUALITY_WEIGHT * adj.get("growth_quality", 1.0)
    w_persistence = PERSISTENCE_WEIGHT * adj.get("persistence", 1.0)
    w_insider = INSIDER_BUYS_WEIGHT * adj.get("insider_buys", 1.0)

    # 1. Relative Volume (base weight: 4.0)
    if current_volume and average_volume and average_volume > 0:
        rel_vol = current_volume / average_volume
        if rel_vol >= 2.0:  # 2x average = strong signal
            adjustment = w_rel_vol
        elif rel_vol >= 1.5:
            adjustment = w_rel_vol * 0.6
        elif rel_vol <= 0.5:
            adjustment = -w_rel_vol * 0.5
        else:
            adjustment = 0
        score += adjustment
        reasons.append(f'rel_vol={rel_vol:.2f}x ({adjustment:+.1f})')

    # 2. 1d Price Confirmation (base weight: 50.0) - HIGHEST WEIGHT
    if current_price and previous_close and previous_close > 0:
        price_change_pct = (current_price - previous_close) / previous_close
        # Scale: ±5% = full points
        # Formula: change_pct * 20 * weight
        adjustment = max(-w_price_1d,
                        min(w_price_1d,
                            price_change_pct * 20 * w_price_1d))
        score += adjustment
        reasons.append(f'1d_price={price_change_pct:+.1%} ({adjustment:+.1f})')

    # 3. Analyst Views (base weight: 40.0) - Scaled up from ±3
    if recommendation is not None:
        if recommendation <= 2.2:  # Strong buy/buy
            adjustment = w_analyst * 0.5  # +20
        elif recommendation >= 3.5:  # Sell/strong sell
            adjustment = -w_analyst * 0.5  # -20
        else:
            # Linear scale between 2.2 and 3.5
            adjustment = (3.5 - recommendation) / 1.3 * w_analyst * 0.5
        score += adjustment
        reasons.append(f'analyst={recommendation:.1f} ({adjustment:+.1f})')

    # 4. Growth Quality (base weight: 40.0)
    if revenue_yoy is not None and gross_margin is not None and operating_margin is not None:
        # Combine revenue growth with margin quality
        quality = (revenue_yoy * 50) + (gross_margin * 20) + (operating_margin * 30)
        adjustment = max(-w_growth * 0.5,
                        min(w_growth * 0.5, quality))
        score += adjustment
        reasons.append(f'growth_quality={quality:.1f} ({adjustment:+.1f})')

    # 5. Persistence (base weight: 15.0)
    if current_price and ma_50 and ma_200:
        if current_price > ma_50 > ma_200:
            adjustment = w_persistence
            reasons.append('persistence=uptrend (+15.0)')
        elif current_price < ma_50 < ma_200:
            adjustment = -w_persistence
            reasons.append('persistence=downtrend (-15.0)')
        elif current_price > ma_50:
            adjustment = w_persistence * 0.5
            reasons.append('persistence=above_50ma (+7.5)')
        else:
            adjustment = -w_persistence * 0.5
            reasons.append('persistence=below_50ma (-7.5)')
        score += adjustment

    # 6. Insider Buys (base weight: 30.0) - Form 4 filings via Finnhub
    insider_data = get_finnhub_insider_trades(ticker)
    insider_score = insider_data.get('insider_score', 0)
    if INSIDER_BUYS_WEIGHT > 0:
        insider_score = insider_score * (w_insider / INSIDER_BUYS_WEIGHT)
    score += insider_score
    reasons.append(insider_data.get('reason', 'no insider data'))

    # Clamp score before normalization
    raw_score = score
    score = max(0, min(500, round(score, 2)))  # Allow higher range for normalization

    # Normalize to 0-100 range (preserves relative weights)
    MAX_POSSIBLE_SCORE = (
        w_rel_vol * 10 +
        w_price_1d +
        w_analyst +
        w_insider +
        w_growth +
        w_persistence +
        100
    )  # Total: ~315 points

    normalized_score = (score / MAX_POSSIBLE_SCORE) * 100
    normalized_score = max(0, min(100, round(normalized_score, 2)))

    # Data Quality & Missing Source Tracking
    missing_sources = []
    degradation_warnings = []

    sec_has_data = bool(sec and (sec.get('revenue') or sec.get('net_income') or sec.get('operating_income')))
    if not sec_has_data:
        missing_sources.append("SEC EDGAR")
        degradation_warnings.append("SEC quarterly filings unavailable (YoY revenue & operating margins omitted; potential ±28 pt factor impact)")

    yf_has_data = bool(yf and (yf.get('price') or yf.get('financialData') or yf.get('summaryDetail')))
    if not yf_has_data:
        missing_sources.append("Yahoo Finance")
        degradation_warnings.append("Market price & financial snapshot unavailable (price momentum, ratios, and valuation omitted)")
    else:
        if current_price is None:
            degradation_warnings.append("Current market price unavailable from Yahoo Finance (1-day momentum ±50 pt factor omitted)")
        if gross_margin is None and operating_margin is None:
            degradation_warnings.append("Margin metrics unavailable from Yahoo Finance (growth quality factor reduced)")

    if eps_surprise is None:
        missing_sources.append("Alpha Vantage (EPS)")
        degradation_warnings.append("Earnings surprise (EPS) data unavailable (Alpha Vantage factor omitted)")

    if "SEC EDGAR" in missing_sources and "Yahoo Finance" in missing_sources:
        data_quality = "DEGRADED"
    elif missing_sources:
        data_quality = "PARTIAL"
    else:
        data_quality = "FULL"

    is_degraded = (data_quality != "FULL")
    if is_degraded:
        reasons.append(f"data_quality={data_quality} (missing: {', '.join(missing_sources)})")

    result_dict = {
        'ticker': ticker,
        'score': normalized_score,  # Use normalized score
        'raw_score': raw_score,     # Keep raw for debugging
        'data_quality': data_quality,
        'is_degraded': is_degraded,
        'missing_sources': missing_sources,
        'degradation_warnings': degradation_warnings,
        'revenue_yoy': revenue_yoy,
        'net_income': net_income,
        'operating_income': op_income,
        'cash': cash,
        'market_cap': market_cap,
        'debt_to_equity': debt_to_equity,
        'current_ratio': current_ratio,
        'gross_margin': gross_margin,
        'operating_margin': operating_margin,
        'eps_surprise': eps_surprise,
        'recommendation_mean': recommendation,
        'fundamental_reasons': '; '.join(reasons) if reasons else 'baseline (no data)'
    }
    _fundamental_scores_cache[cache_key] = result_dict
    return result_dict


# =============================================================================
# EVIDENCE DOSSIER & XAI SYNTHESIS
# =============================================================================

def build_evidence_dossier(
    ticker: str,
    industry: str,
    catalyst: str,
    fundamentals: Dict[str, Any],
    strategy_track: str = "GROWTH",
    options_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build a multi-dimensional evidence dossier for a candidate stock.

    Includes:
    - Clickable SEC filing URL
    - Financial health & solvency metrics
    - Alternative data catalysts (USAspending awards, Form 4 insider purchases, Congress trades)
    - Options yield metrics (if strategy_track == 'INCOME')
    - 3 Bull Drivers + 3 Risk Warnings (XAI synthesis)
    """
    # 1. SEC Filing URL
    sec_url = "https://www.sec.gov/edgar/searchedgar/companysearch"
    try:
        sec_map = get_sec_ticker_map()
        cik = sec_map.get(ticker.upper())
        if cik:
            sec_url = f"https://www.sec.gov/edgar/browse/?CIK={cik}"
    except Exception as e:
        logger.debug(f"Could not build SEC URL for {ticker}: {e}")

    # 2. Alternative Data (Contracts & Congress)
    alt_data = {}
    alt_data_failed = False
    try:
        from .alternative_data_client import fetch_alternative_catalysts
        alt_data = fetch_alternative_catalysts(ticker)
    except Exception as e:
        logger.debug(f"Alternative data fetch failed for {ticker}: {e}")
        alt_data_failed = True

    contracts = alt_data.get("contracts", [])
    congress_trades = alt_data.get("congress_trades", [])

    # 3. Dynamic XAI Synthesis (3 Bull Drivers, 3 Risk Warnings)
    bull_thesis = []

    if strategy_track == "INCOME" and options_data:
        my = options_data.get("monthly_yield_est")
        if my is not None:
            bull_thesis.append(f"Attractive monthly covered call premium yield of {my:.1f}% ({options_data.get('annualized_yield_est', 0):.1f}% annualized)")

    if catalyst and "No news" not in catalyst and len(bull_thesis) < 3:
        bull_thesis.append(f"Catalyst: {catalyst[:85]}")

    rev_yoy = fundamentals.get("revenue_yoy")
    if rev_yoy is not None and len(bull_thesis) < 3:
        bull_thesis.append(f"Revenue growth of {rev_yoy * 100:.1f}% YoY with strong top-line momentum")
    elif len(bull_thesis) < 3:
        bull_thesis.append("Solid fundamental baseline with stable quarterly operations")

    if contracts:
        top_contract = contracts[0]
        amt_str = f"${top_contract.get('amount', 0):,.0f}" if top_contract.get('amount') else "Federal"
        bull_thesis.append(f"Awarded {amt_str} contract from {top_contract.get('agency', 'Gov')}")
    elif congress_trades:
        bull_thesis.append(f"Recent Congressional purchase disclosure by {congress_trades[0].get('representative', 'Member of Congress')}")
    else:
        insider = fundamentals.get("insider_shares_net")
        if insider and insider > 0:
            bull_thesis.append(f"Net positive insider buying of {insider:,.0f} shares")
        else:
            bull_thesis.append(f"High sector conviction in {industry}")

    risk_warnings = []
    de = fundamentals.get("debt_to_equity")
    if de is not None and de > 150:
        risk_warnings.append(f"Elevated Debt-to-Equity ratio ({de:.0f}%) requires debt service monitoring")
    else:
        risk_warnings.append("Subject to broader macro interest rate and market beta volatility")

    cr = fundamentals.get("current_ratio")
    if cr is not None and cr < 1.1:
        risk_warnings.append(f"Current ratio is tight at {cr:.2f} (working capital constraint)")
    else:
        risk_warnings.append("Execution risk in delivering against backlog and operating guidance")

    if strategy_track == "INCOME" and options_data:
        dte = options_data.get("days_to_earnings")
        if dte is not None and dte <= 30:
            risk_warnings.append(f"Earnings scheduled in {dte} days - covered call assignment risk elevated")
        else:
            risk_warnings.append("Stock price upside capped by short call strike")
    else:
        risk_warnings.append("Fast momentum can experience sharp mean-reverting pullbacks")

    # Collect missing sources and degradation warnings from fundamentals & dossier
    dossier_missing = list(fundamentals.get("missing_sources", []))
    dossier_warnings = list(fundamentals.get("degradation_warnings", []))

    if alt_data_failed:
        dossier_missing.append("USAspending / Congress Catalysts")
        dossier_warnings.append("Alternative catalyst data feeds unavailable (public contracts and congressional filings unverified)")

    if strategy_track == "INCOME":
        if not options_data or options_data.get("monthly_yield_est") is None:
            dossier_missing.append("Options Chain Greeks")
            dossier_warnings.append("Covered call options Greeks / premium yield unverified")
        elif options_data.get("days_to_earnings") is None:
            dossier_missing.append("Earnings Calendar")
            dossier_warnings.append("Earnings date unavailable - binary event risk unverified")

    # Determine data quality
    data_quality = fundamentals.get("data_quality", "FULL")
    if "SEC EDGAR" in dossier_missing and "Yahoo Finance" in dossier_missing:
        data_quality = "DEGRADED"
    elif len(dossier_missing) >= 3:
        data_quality = "DEGRADED"
    elif dossier_missing:
        data_quality = "PARTIAL"

    is_degraded = (data_quality != "FULL") or bool(dossier_warnings)

    if is_degraded:
        # Prepend a clear warning to risk_warnings so that the operator and downstream sentinels know
        risk_warnings.insert(0, f"⚠️ Scoring Degraded: {', '.join(dossier_missing) if dossier_missing else 'Missing key data feeds'}")

    # Determine Solvency Rating
    cr = fundamentals.get("current_ratio")
    de = fundamentals.get("debt_to_equity")
    solvency = "Adequate"
    if cr is not None and de is not None:
        if cr >= 1.5 and de < 80:
            solvency = "Pristine"
        elif cr >= 1.2 and de < 150:
            solvency = "Robust"
        elif cr < 1.0 or de > 250:
            solvency = "Distressed"

    dossier = {
        "sec_filing_url": sec_url,
        "data_quality": data_quality,
        "is_degraded": is_degraded,
        "missing_sources": dossier_missing,
        "degradation_warnings": dossier_warnings,
        "revenue_yoy": fundamentals.get("revenue_yoy"),
        "net_income": fundamentals.get("net_income"),
        "operating_income": fundamentals.get("operating_income"),
        "gross_margin": fundamentals.get("gross_margin"),
        "operating_margin": fundamentals.get("operating_margin"),
        "debt_to_equity": fundamentals.get("debt_to_equity"),
        "current_ratio": fundamentals.get("current_ratio"),
        "solvency_rating": solvency,
        "contracts": contracts,
        "usaspending_contracts": contracts,
        "contract_count": len(contracts),
        "total_contract_value": alt_data.get("total_contract_value", 0),
        "congress_trades": congress_trades,
        "congressional_trades": congress_trades,
        "congress_buy_count": len(congress_trades),
        "bull_thesis": bull_thesis[:3],
        "bull_drivers": bull_thesis[:3],
        "risk_warnings": risk_warnings[:3],
    }

    if options_data:
        clean_opt = {k: v for k, v in options_data.items() if k != "evidence_dossier"}
        dossier["options_yield_metrics"] = clean_opt
        dossier.update({
            "monthly_yield_est": clean_opt.get("monthly_yield_est"),
            "annualized_yield_est": clean_opt.get("annualized_yield_est"),
            "strike": clean_opt.get("strike"),
            "open_interest": clean_opt.get("open_interest"),
            "days_to_earnings": clean_opt.get("days_to_earnings"),
            "earnings_risk_flag": clean_opt.get("earnings_risk_flag", False),
        })

    return dossier


# =============================================================================
# PICK SELECTION (Composite Scoring)
# =============================================================================

def score_candidates(
    ranked_news: List[Dict],
    weight_adjustments: Optional[Dict[str, float]] = None
) -> List[Dict]:
    """
    Score candidate tickers and select top picks with Evidence Dossiers.
    Optionally incorporates dynamic weight adjustments from the Factor IC ledger.
    """
    picks = []

    for news_item in ranked_news:
        # Check if this is fundamentals-only mode
        is_fundamentals_only = news_item.get('news_source') == 'fundamentals_only'

        # Only consider highly explosive news (7.5+ threshold) unless in fundamentals-only mode
        explosiveness = float(news_item.get('explosiveness', 0))
        if not is_fundamentals_only and explosiveness < EXPLOSIVENESS_THRESHOLD:
            continue  # Skip low-impact news (but allow fundamentals-only picks through)

        industry = news_item.get('industry', '')
        candidates = news_item.get('impacted_us_tickers', [])

        # Fallback: use direct ticker if no candidate list
        if not candidates:
            direct = news_item.get('direct_ticker')
            if direct:
                candidates = [direct]

        if not candidates:
            logger.warning(f"No candidates for industry {industry}, skipping")
            continue

        # Score each candidate ticker in this industry
        best_ticker = None
        best_score = -1
        best_fundamentals = {}

        for ticker in candidates:
            if not ticker:
                continue

            try:
                # Get fundamental analysis for this ticker (with dynamic weights if present)
                fundamentals = compute_fundamental_score(ticker, weight_adjustments=weight_adjustments)
                fundamental_score = fundamentals.get('score', BASELINE_FUNDAMENTAL_SCORE)

                # Calculate composite score: 60% explosiveness + 40% fundamentals
                composite_score = (explosiveness * (EXPLOSIVENESS_WEIGHT * 10)) + \
                                (fundamental_score * FUNDAMENTAL_WEIGHT)

                logger.debug(
                    f"{ticker}: explosive={explosiveness:.1f}, "
                    f"fund={fundamental_score:.0f}, "
                    f"composite={composite_score:.1f}"
                )

                # Track best ticker for this industry
                if composite_score > best_score:
                    best_score = composite_score
                    best_ticker = ticker
                    best_fundamentals = fundamentals

            except Exception as e:
                logger.warning(f"Failed to score {ticker}: {e}")
                continue

        # Fallback: if all scorings failed, use first candidate with baseline
        if best_ticker is None and candidates:
            best_ticker = candidates[0]
            best_score = (explosiveness * (EXPLOSIVENESS_WEIGHT * 10)) + \
                        (BASELINE_FUNDAMENTAL_SCORE * FUNDAMENTAL_WEIGHT)
            best_fundamentals = {
                'ticker': best_ticker,
                'score': BASELINE_FUNDAMENTAL_SCORE,
                'fundamental_reasons': 'No data - baseline score (all APIs failed)'
            }
            logger.warning(f"Using fallback scoring for {best_ticker}")

        # Add the best pick for this industry
        if best_ticker:
            catalyst_text = news_item.get('headline', '')
            dossier = build_evidence_dossier(
                ticker=best_ticker,
                industry=industry,
                catalyst=catalyst_text,
                fundamentals=best_fundamentals,
                strategy_track="GROWTH"
            )

            is_degraded = dossier.get('is_degraded', False)
            missing_sources = dossier.get('missing_sources', [])
            degradation_warnings = dossier.get('degradation_warnings', [])
            data_quality = dossier.get('data_quality', 'FULL')

            rat_base = f"{news_item.get('rationale_short', '')} | {news_item.get('thesis', '')}"
            if is_degraded and missing_sources:
                rat_base += f" | [⚠️ Degraded: missing {', '.join(missing_sources)}]"

            pick = {
                'industry': industry,
                'ticker': best_ticker,
                'catalyst': catalyst_text,
                'explosiveness': explosiveness,
                'fundamental_score': best_fundamentals.get('score'),
                'composite_score': round(best_score, 2),
                'strategy_track': 'GROWTH',
                'status': 'PENDING_REVIEW',
                'evidence_dossier': dossier,
                'data_quality': data_quality,
                'is_degraded': is_degraded,
                'missing_sources': missing_sources,
                'degradation_warnings': degradation_warnings,

                # Financial metrics (may be None if data unavailable)
                'revenue_yoy': best_fundamentals.get('revenue_yoy'),
                'net_income': best_fundamentals.get('net_income'),
                'operating_income': best_fundamentals.get('operating_income'),
                'gross_margin': best_fundamentals.get('gross_margin'),
                'operating_margin': best_fundamentals.get('operating_margin'),
                'debt_to_equity': best_fundamentals.get('debt_to_equity'),
                'current_ratio': best_fundamentals.get('current_ratio'),
                'eps_surprise': best_fundamentals.get('eps_surprise'),
                'recommendation_mean': best_fundamentals.get('recommendation_mean'),

                # Rationale combining news and fundamentals
                'fundamental_reasons': best_fundamentals.get('fundamental_reasons', ''),
                'rationale': rat_base
            }
            picks.append(pick)

    # Sort by composite score (highest first)
    picks.sort(key=lambda x: x['composite_score'], reverse=True)

    logger.info(
        f"Generated {len(picks)} candidate picks "
        f"(filtered from {len(ranked_news)} news items)"
    )

    return picks
