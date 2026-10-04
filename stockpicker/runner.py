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
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from google.cloud import firestore

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
        List of active source names (e.g., ['sec_edgar', 'yahoo_finance', 'gemini'])

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
    if os.getenv('GEMINI_API_KEY'):
        sources.append('gemini')

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



def load_registered_bots() -> Dict[str, Dict]:
    """
    Load all registered bots from bots.json to check active portfolio symbols.
    """
    config_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "tradingbot-config", "bots.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "bots.json"),
        os.path.join(os.getcwd(), "tradingbot-config", "bots.json"),
    ]
    for p in config_paths:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                    bots = data.get("bots", [])
                    return {b.get("symbol", "").upper(): b for b in bots if b.get("symbol")}
            except Exception as e:
                logger.warning(f"Error reading {p}: {e}")
    return {}


def hydrate_history_state(db: Optional[firestore.Client]) -> Dict[str, Any]:
    """
    Hydrate previously accepted and rejected picks from Firestore.
    """
    state = {
        "accepted": {},  # symbol -> dict
        "rejected": {}   # symbol -> dict
    }
    if not db:
        return state
    try:
        doc = db.collection('stock_picks').document('current').get()
        if doc.exists:
            data = doc.to_dict() or {}
            # Track already accepted / provisioned entries
            for item in data.get('already_accepted', []):
                sym = (item.get('ticker') or item.get('symbol') or "").upper()
                if sym:
                    state['accepted'][sym] = item

            for item in data.get('picks', []):
                sym = (item.get('ticker') or item.get('symbol') or "").upper()
                if not sym:
                    continue
                st = item.get('status')
                if st in ('ACCEPTED', 'PROVISIONED'):
                    state['accepted'][sym] = item
                elif st == 'REJECTED':
                    state['rejected'][sym] = item

            for item in data.get('rejected_cooldown', []):
                sym = (item.get('ticker') or item.get('symbol') or "").upper()
                if sym:
                    state['rejected'][sym] = item
    except Exception as e:
        logger.warning(f"Could not hydrate history state from Firestore: {e}")
    return state


# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_stockpicker() -> Optional[Dict[str, Any]]:
    """
    Main entry point for StockPicker execution.

    Orchestrates the complete dual-track pipeline:
    1. Hydrate state: Active bots (bots.json) and past review decisions (Firestore)
    2. Fetch news from all available sources
    3. Rank news by explosiveness (Google Gemini Flash LLM or heuristic)
    4. Track 1: Score Growth Equity candidates with Evidence Dossiers
    5. Track 2: Screen Covered Call Income candidates with Options Analytics
    6. State Awareness & Promotion:
       - Symbols already accepted or active on a bot are stamped 'PROVISIONED'
         and moved to 'already_accepted' (retaining current telemetry and bot link).
       - Symbols in 7-day rejection cooldown are suppressed unless breakthrough catalyst.
       - Fresh un-provisioned candidates are promoted into top-5 actionable review slots.
    7. Write state-partitioned results to Firestore ('stock_picks/current' & archive).

    Returns:
        Structured dictionary with actionable_leads, growth_picks, income_picks,
        already_accepted, rejected_cooldown, and summary.
    """
    logger.info("=" * 60)
    logger.info("StockPicker run starting (Dual-Track State-Aware Pipeline)...")
    logger.info(f"Active sources: {', '.join(detect_active_sources())}")
    logger.info("=" * 60)

    # Initialize Firestore client
    db = firestore.Client()

    try:
        # Step 0: Hydrate Active Portfolio & Review History
        logger.info("Step 0: Hydrating active bot registry and review history...")
        registered_bots = load_registered_bots()
        history_state = hydrate_history_state(db)
        logger.info(f"✓ Found {len(registered_bots)} registered bots, {len(history_state['accepted'])} accepted, {len(history_state['rejected'])} rejected in history")

        # Step 1: Fetch news from last 24 hours
        logger.info("Step 1/5: Fetching news from last 24 hours...")
        news = fetch_all_news(hours=24)
        logger.info(f"✓ Fetched {len(news)} news items from all sources")

        # Step 2: Rank news by explosiveness
        logger.info("Step 2/5: Ranking news by explosiveness...")
        ranked = rank_news(news) if news else []
        logger.info(f"✓ Ranked {len(ranked)} items")

        # Step 3: Track 1 - Growth Equity Scoring
        logger.info("Step 3/5: Scoring Track 1 (Growth Equity) candidates...")
        growth_candidates = score_candidates(ranked) if ranked else []
        logger.info(f"✓ Scored {len(growth_candidates)} growth candidates")

        # Step 4: Track 2 - Covered Call Income Screening
        logger.info("Step 4/5: Screening Track 2 (Covered Call Income) candidates...")
        from stockpicker.income_screener import screen_income_candidates
        from stockpicker.core import build_evidence_dossier
        income_candidates = screen_income_candidates(top_n=15)

        # Attach Evidence Dossiers to Income candidates
        for inc in income_candidates:
            sym = inc.get("ticker", "")
            dossier = build_evidence_dossier(
                ticker=sym,
                industry="Covered Call Income",
                catalyst=f"Est. Monthly Yield: {inc.get('monthly_yield_est', 0)}% (Annualized: {inc.get('annualized_yield_est', 0)}%)",
                fundamentals={
                    "current_price": inc.get("current_price"),
                    "implied_volatility": inc.get("implied_volatility"),
                    "open_interest": inc.get("open_interest"),
                },
                strategy_track="INCOME",
                options_data=inc
            )
            inc["evidence_dossier"] = dossier
            inc["composite_score"] = inc.get("income_score", 50.0)
            inc["status"] = "PENDING_REVIEW"
        logger.info(f"✓ Screened {len(income_candidates)} income candidates with dossiers")

        # Step 5: Deduping, State-Aware Partitioning, and Lead Promotion
        logger.info("Step 5/5: Partitioning candidates by state (Deduping active & cooldown)...")
        now_utc = datetime.now(timezone.utc)
        seven_days_ago = now_utc - timedelta(days=7)

        already_accepted = []
        rejected_cooldown = []
        actionable_growth = []
        actionable_income = []

        seen_symbols = set()

        def process_candidate(cand: Dict[str, Any], track: str):
            sym = (cand.get("ticker") or cand.get("symbol") or "").upper()
            if not sym or sym in seen_symbols:
                return
            seen_symbols.add(sym)

            cand["strategy_track"] = track
            cand["ticker"] = sym
            cand["symbol"] = sym

            # Case 1: Active Bot or Previously Accepted
            if sym in registered_bots or sym in history_state["accepted"]:
                bot_info = registered_bots.get(sym, {})
                prev_info = history_state["accepted"].get(sym, {})
                bot_id = bot_info.get("name") or prev_info.get("bot_id") or f"{sym.lower()}_sma"
                client_id = bot_info.get("client_id") or prev_info.get("client_id", "active")

                current_score = cand.get("composite_score", 0.0)
                initial_score = prev_info.get("score") or prev_info.get("composite_score") or current_score
                score_delta = round(current_score - initial_score, 2)

                cand["status"] = "PROVISIONED"
                cand["bot_id"] = bot_id
                cand["client_id"] = client_id
                cand["provisioned_at"] = prev_info.get("provisioned_at") or now_utc.isoformat()
                cand["current_score"] = current_score
                cand["initial_score"] = initial_score
                cand["score_delta"] = f"+{score_delta}" if score_delta >= 0 else str(score_delta)
                cand["status_label"] = f"Bot Active (client_id: {client_id})"
                cand["dashboard_link"] = f"/bot/{bot_id}"

                already_accepted.append(cand)
                return

            # Case 2: Rejected within 7 days cooldown
            if sym in history_state["rejected"]:
                rej = history_state["rejected"][sym]
                rej_at_str = rej.get("rejected_at")
                is_in_cooldown = True
                if rej_at_str:
                    try:
                        rej_dt = datetime.fromisoformat(rej_at_str.replace("Z", "+00:00"))
                        if rej_dt < seven_days_ago:
                            is_in_cooldown = False
                    except Exception:
                        pass

                # Check breakthrough override (score jump >= 20 pts)
                score_jump = cand.get("composite_score", 0.0) - (rej.get("score") or 50.0)
                if is_in_cooldown and score_jump < 20.0:
                    cand["status"] = "REJECTED"
                    cand["rejection_reason"] = rej.get("rejection_reason", "Operator rejected")
                    cand["rejected_at"] = rej_at_str or now_utc.isoformat()
                    rejected_cooldown.append(cand)
                    return

            # Case 3: Fresh Actionable Lead
            cand["status"] = "PENDING_REVIEW"
            cand["is_new_lead"] = True
            if track == "GROWTH":
                actionable_growth.append(cand)
            else:
                actionable_income.append(cand)

        # Process Growth candidates first
        for g in growth_candidates:
            process_candidate(g, "GROWTH")

        # Process Income candidates
        for inc in income_candidates:
            process_candidate(inc, "INCOME")

        # Top 5 actionable per track
        growth_picks = actionable_growth[:5]
        income_picks = actionable_income[:5]
        actionable_leads = growth_picks + income_picks

        # Backward compatibility picks array
        picks = actionable_leads[:5] if actionable_leads else (already_accepted[:5] if already_accepted else [])

        # Calculate average explosiveness
        avg_explosive = 0.0
        if picks:
            explosiveness_vals = [p.get("explosiveness", 0.0) for p in picks if p.get("explosiveness") is not None]
            if explosiveness_vals:
                avg_explosive = sum(explosiveness_vals) / len(explosiveness_vals)

        summary = {
            "growth_actionable_count": len(growth_picks),
            "income_actionable_count": len(income_picks),
            "already_accepted_count": len(already_accepted),
            "rejected_cooldown_count": len(rejected_cooldown),
        }

        # Step 6: Write to Firestore
        logger.info("Writing state-aware results to Firestore (stock_picks/current)...")
        now_iso = now_utc.date().isoformat()
        stock_picks_ref = db.collection('stock_picks')

        result_payload = {
            'picks': picks,
            'actionable_leads': actionable_leads,
            'growth_picks': growth_picks,
            'income_picks': income_picks,
            'already_accepted': already_accepted,
            'rejected_cooldown': rejected_cooldown,
            'summary': summary,
            'run_timestamp': firestore.SERVER_TIMESTAMP,
            'pick_count': len(picks),
            'avg_explosiveness': round(avg_explosive, 2),
            'sources_used': detect_active_sources(),
            'status': 'success'
        }

        # Write to 'current' document
        stock_picks_ref.document('current').set(result_payload)

        # Archive under current date
        try:
            stock_picks_ref.document(now_iso).set(result_payload)
        except Exception as arc_err:
            logger.warning(f"Could not archive daily stock picks: {arc_err}")

        logger.info("=" * 60)
        logger.info("✅ StockPicker state-aware run completed successfully!")
        logger.info(f"   Actionable Growth Leads: {len(growth_picks)}")
        logger.info(f"   Actionable Income Leads: {len(income_picks)}")
        logger.info(f"   Already Accepted (Active): {len(already_accepted)}")
        logger.info(f"   Rejected in Cooldown: {len(rejected_cooldown)}")
        logger.info("=" * 60)

        return result_payload

    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ StockPicker run failed with exception:")
        logger.exception(e)
        logger.error("=" * 60)
        write_error_result(db, e)
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
