"""
StockPicker Runner
Entry point for on-demand and scheduled execution

This orchestrates the full StockPicker pipeline:
1. Fetch news from all available sources (last 24 hours)
2. Rank news by explosiveness (LLM or heuristic)
3. Score candidate tickers with fundamental analysis
4. Select top 5 picks
5. Write results to Firestore

Features:
- Comprehensive logging with progress tracking
- Graceful error handling with Firestore status updates
- Works with any combination of API keys (0-7 sources)
- Returns consistent structure for both success and failure cases
"""

import os
import sys
import logging
from google.cloud import firestore
from datetime import datetime
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockpicker.core import fetch_all_news, rank_news, score_candidates

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

def setup_logging():
    """
    Configure logging for StockPicker runner.

    Logs to both console (stdout) and file (if writable).
    Uses Cloud Run compatible paths (/tmp for ephemeral storage).
    """
    # Determine log directory (Cloud Run uses /tmp for writable storage)
    log_dir = os.getenv('LOG_DIR', '/tmp')
    log_file = os.path.join(log_dir, 'stockpicker.log')

    # Start with console handler (always available)
    handlers = [logging.StreamHandler()]

    # Try to add file handler (may fail in restricted environments)
    try:
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, mode='a'))
    except Exception as e:
        # File logging not available (not critical)
        print(f"Warning: Could not create log file at {log_file}: {e}")

    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers,
        force=True  # Override any existing configuration
    )

setup_logging()
logger = logging.getLogger(__name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def detect_active_sources() -> List[str]:
    """
    Detect which API sources are configured via environment variables.

    Returns:
        List of active source names (e.g., ['sec_edgar', 'yahoo_finance', 'openai'])

    Note:
        'sec_edgar' and 'yahoo_finance' are always included (free, no API key).
        All other sources require API keys to be active.
    """
    # Free sources (always available)
    sources = ['sec_edgar', 'yahoo_finance']

    # Optional news sources
    if os.getenv('NEWSAPI_KEY'):
        sources.append('newsapi')
    if os.getenv('X_BEARER_TOKEN'):
        sources.append('twitter')
    if os.getenv('POLYGON_API_KEY'):
        sources.append('polygon')

    # Optional ranking enhancement
    if os.getenv('OPENAI_API_KEY'):
        sources.append('openai')

    # Optional fundamental enhancement
    if os.getenv('ALPHAVANTAGE_API_KEY'):
        sources.append('alphavantage')

    return sources


def write_empty_result(db: firestore.Client, message: str, status: str = 'no_news'):
    """
    Write empty result to Firestore (no picks generated).

    Args:
        db: Firestore client
        message: Human-readable status message
        status: Machine-readable status code

    Note:
        This is not an error - it's a valid result when no explosive news found.
    """
    try:
        db.collection('stock_picks').document('current').set({
            'picks': [],
            'run_timestamp': firestore.SERVER_TIMESTAMP,
            'pick_count': 0,
            'avg_explosiveness': 0.0,
            'sources_used': detect_active_sources(),
            'message': message,
            'status': status
        })
        logger.info(f"Wrote empty result to Firestore: {message}")
    except Exception as e:
        logger.error(f"Failed to write empty result to Firestore: {e}")


def write_error_result(db: firestore.Client, error: Exception):
    """
    Write error status to Firestore.

    Args:
        db: Firestore client
        error: Exception that caused the failure

    Note:
        This ensures the dashboard shows error state rather than stale data.
    """
    try:
        db.collection('stock_picks').document('current').set({
            'picks': [],
            'run_timestamp': firestore.SERVER_TIMESTAMP,
            'pick_count': 0,
            'avg_explosiveness': 0.0,
            'sources_used': detect_active_sources(),
            'error': str(error),
            'status': 'error'
        })
        logger.info("Wrote error status to Firestore")
    except Exception as db_error:
        logger.error(f"Failed to write error status to Firestore: {db_error}")


# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_stockpicker() -> Optional[List[Dict]]:
    """
    Main entry point for StockPicker execution.

    This function orchestrates the complete pipeline:
    1. Fetch news from all available sources (NewsAPI, X, Polygon)
    2. Rank news by explosiveness (OpenAI LLM or heuristic)
    3. Score candidate tickers using fundamental analysis
    4. Select top 5 picks by composite score
    5. Write results to Firestore

    Returns:
        List of top 5 picks (dicts) if successful
        None if no picks generated (not an error)
        Raises exception on fatal errors

    Note:
        Results are always written to Firestore (stock_picks/current document).
        The dashboard reads from this document to display picks.

    Expected Duration:
        - Without API keys: 5-10 seconds (no news sources)
        - With NewsAPI only: 30-45 seconds
        - With OpenAI + NewsAPI: 45-60 seconds
        - Full configuration: 60-90 seconds
    """
    logger.info("=" * 60)
    logger.info("StockPicker run starting...")
    logger.info(f"Active sources: {', '.join(detect_active_sources())}")
    logger.info("=" * 60)

    # Initialize Firestore client
    db = firestore.Client()

    try:
        # =================================================================
        # STEP 1: Fetch news from all available sources
        # =================================================================
        logger.info("Step 1/4: Fetching news from last 24 hours...")
        news = fetch_all_news(hours=24)
        logger.info(f"✓ Fetched {len(news)} news items from all sources")

        # Log warning if no news, but continue with fundamentals-only mode
        if not news:
            logger.warning("⚠️ No recent news fetched - using fundamentals-only mode")
            # Continue with empty news list - fundamentals will drive picks

        # =================================================================
        # STEP 2: Rank news by explosiveness
        # =================================================================
        logger.info("Step 2/4: Ranking news by explosiveness...")
        ranked = rank_news(news)
        logger.info(f"✓ Ranked {len(ranked)} items")

        # Early exit if ranking failed
        if not ranked:
            logger.warning("No ranked items produced - ending run")
            write_empty_result(
                db,
                message='News ranking failed (check API keys)',
                status='ranking_failed'
            )
            return None

        # Log explosiveness distribution for debugging
        explosive_count = sum(1 for item in ranked if item.get('explosiveness', 0) >= 7.5)
        logger.info(
            f"   Distribution: {explosive_count} explosive (≥7.5), "
            f"{len(ranked) - explosive_count} non-explosive (<7.5)"
        )

        # =================================================================
        # STEP 3: Score candidates and select top 5
        # =================================================================
        logger.info("Step 3/4: Scoring candidate tickers with fundamental analysis...")
        top_5 = score_candidates(ranked)
        logger.info(f"✓ Selected {len(top_5)} picks")

        # Early exit if no picks meet threshold
        if not top_5:
            logger.warning("No picks met threshold - ending run (not an error)")
            write_empty_result(
                db,
                message='No explosive news found (explosiveness < 7.5 threshold)',
                status='no_explosive_news'
            )
            return None

        # =================================================================
        # STEP 4: Write results to Firestore
        # =================================================================
        logger.info("Step 4/4: Writing results to Firestore...")

        # Calculate average explosiveness for metrics
        avg_explosive = sum(p['explosiveness'] for p in top_5) / len(top_5)

        # Write to Firestore (stock_picks/current document)
        stock_picks_ref = db.collection('stock_picks')
        stock_picks_ref.document('current').set({
            'picks': top_5,
            'run_timestamp': firestore.SERVER_TIMESTAMP,
            'pick_count': len(top_5),
            'avg_explosiveness': round(avg_explosive, 2),
            'sources_used': detect_active_sources(),
            'status': 'success'
        })

        # =================================================================
        # SUCCESS - Log summary
        # =================================================================
        logger.info("=" * 60)
        logger.info("✅ StockPicker run completed successfully!")
        logger.info(f"   Picks generated: {len(top_5)}")
        logger.info(f"   Avg explosiveness: {avg_explosive:.2f}")

        if top_5:
            top_pick = top_5[0]
            logger.info(
                f"   Top pick: {top_pick['ticker']} "
                f"({top_pick['industry']}, "
                f"score: {top_pick['composite_score']:.1f})"
            )
            logger.info(f"   Catalyst: {top_pick['catalyst'][:80]}...")

        logger.info("=" * 60)

        return top_5

    except Exception as e:
        # =================================================================
        # ERROR HANDLING - Log and write error status
        # =================================================================
        logger.error("=" * 60)
        logger.error(f"❌ StockPicker run failed with exception:")
        logger.exception(e)  # Log full traceback
        logger.error("=" * 60)

        # Write error status to Firestore
        write_error_result(db, e)

        # Re-raise exception for caller to handle
        raise


# =============================================================================
# COMMAND-LINE EXECUTION
# =============================================================================

if __name__ == '__main__':
    """
    Command-line entry point for manual or cron execution.

    Usage:
        python -m stockpicker.runner

    Exit codes:
        0: Success (picks generated or no explosive news)
        1: Fatal error (exception raised)
    """
    try:
        result = run_stockpicker()

        # Success cases (both are valid outcomes)
        if result:
            print(f"\n✅ Success: Generated {len(result)} picks")
            sys.exit(0)
        else:
            print("\n✅ Success: No picks generated (no explosive news)")
            sys.exit(0)

    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
