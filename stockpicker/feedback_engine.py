"""
StockPicker Closed-Loop Feedback Engine (Phase 7)

Implements the self-healing, self-improving feedback architecture from
Section 7 of the QUIVERQUANT_STOCKPICKER_ENHANCEMENT_DESIGN.md:

  Loop A: Market Outcome Feedback (Forward Alpha Tracking & Factor IC)
  Loop B: Trading Engine Execution Feedback (Realized P&L Backpropagation)

Firestore Collections:
  - stock_picks_archive/{date}_{ticker}: Immutable per-pick snapshots
  - stock_picks_performance/{pick_id}: Forward alpha at t+1, t+5, t+20
  - stockpicker_execution_feedback/{pick_id}: Trade P&L from bots
  - stockpicker_factor_ledger/current: Factor IC scores & weight overrides
  - stockpicker_rejection_analysis/current: Rejection pattern aggregates

Author: Trading Bot Core Team / AI Pair Programmer
Created: October 4, 2026
"""

import os
import sys
import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

# Forward alpha evaluation horizons (in trading days)
ALPHA_HORIZONS = [1, 5, 20]

# Benchmark tickers for excess return calculation
BENCHMARKS = ["SPY", "IWM"]

# Factor IC rolling window (days)
IC_ROLLING_WINDOW_DAYS = 60

# Weight adaptation limits: factors can be boosted/penalized by at most ±20%
MAX_WEIGHT_BOOST_PCT = 0.20
MAX_WEIGHT_DECAY_PCT = 0.20

# IC thresholds for weight adjustment (from design doc Section 7.1)
IC_BOOST_THRESHOLD = 0.05   # IC > +0.05 → boost weight
IC_DECAY_THRESHOLD = 0.00   # IC <= 0.00 → decay weight

# Archive TTL: records older than this are auto-purged
ARCHIVE_TTL_DAYS = 90

# Maximum archive documents to process in a single cleanup run
MAX_CLEANUP_BATCH = 500

# Factor names as tracked in the scoring engine
TRACKED_FACTORS = [
    "relative_volume",
    "price_momentum_1d",
    "analyst_views",
    "insider_buys",
    "congress_buys",
    "growth_quality",
    "persistence",
    "explosiveness",
    "revenue_yoy",
    "operating_margin",
    "debt_to_equity",
]


# =============================================================================
# IMMUTABLE SNAPSHOT ARCHIVING (Design Doc Section 7.1)
# =============================================================================

def archive_pick_snapshot(
    db,
    candidate: Dict[str, Any],
    run_date: str,
    entry_price: Optional[float] = None,
) -> Optional[str]:
    """
    Write an immutable per-pick snapshot to Firestore.

    Each accepted or actionable candidate is archived with its full factor
    sub-scores, catalyst metadata, and entry price at the time of generation.
    This creates the ground truth record against which forward alpha is measured.

    Firestore path: stock_picks_archive/{date}_{ticker}

    Args:
        db: Firestore client
        candidate: Full candidate dict from the scoring pipeline
        run_date: ISO date string (YYYY-MM-DD) of the run
        entry_price: Price at time of pick generation (snapshot price)

    Returns:
        The pick_id string (e.g., "2026-10-05_KTOS") or None on failure
    """
    if not db:
        logger.warning("No Firestore client - cannot archive snapshot")
        return None

    ticker = (candidate.get("ticker") or candidate.get("symbol") or "").upper()
    if not ticker:
        logger.warning("Cannot archive snapshot: no ticker in candidate")
        return None

    pick_id = f"{run_date}_{ticker}"

    # Extract factor sub-scores from fundamental_reasons string for IC tracking
    factor_scores = _extract_factor_scores(candidate)

    snapshot = {
        "pick_id": pick_id,
        "symbol": ticker,
        "ticker": ticker,
        "run_date": run_date,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "strategy_track": candidate.get("strategy_track", "GROWTH"),
        "status_at_archive": candidate.get("status", "PENDING_REVIEW"),

        # Scores
        "composite_score": candidate.get("composite_score", 0.0),
        "fundamental_score": candidate.get("fundamental_score", 0.0),
        "explosiveness": candidate.get("explosiveness", 0.0),
        "income_score": candidate.get("income_score"),

        # Factor sub-scores (for IC calculation)
        "factor_scores": factor_scores,

        # Entry price snapshot
        "entry_price": entry_price or candidate.get("current_price")
                       or _safe_nested_get(candidate, "evidence_dossier", "current_price"),

        # Key metrics at time of pick
        "revenue_yoy": candidate.get("revenue_yoy"),
        "operating_margin": candidate.get("operating_margin"),
        "gross_margin": candidate.get("gross_margin"),
        "debt_to_equity": candidate.get("debt_to_equity"),
        "current_ratio": candidate.get("current_ratio"),
        "eps_surprise": candidate.get("eps_surprise"),

        # Data quality at time of pick
        "data_quality": candidate.get("data_quality", "FULL"),
        "is_degraded": candidate.get("is_degraded", False),
        "missing_sources": candidate.get("missing_sources", []),

        # Evidence dossier summary (compact version for archive)
        "catalyst": candidate.get("catalyst", ""),
        "rationale": candidate.get("rationale", ""),
        "bull_drivers": _safe_nested_get(candidate, "evidence_dossier", "bull_drivers") or [],
        "risk_warnings": _safe_nested_get(candidate, "evidence_dossier", "risk_warnings") or [],

        # Forward alpha tracking fields (populated later by alpha_auditor)
        "alpha_t1": None,
        "alpha_t5": None,
        "alpha_t20": None,
        "benchmark_returns": {},
        "forward_prices": {},
        "alpha_last_updated": None,

        # Execution feedback fields (populated later by execution feedback loop)
        "execution_linked": False,
        "realized_pnl": None,
        "trade_duration_days": None,
        "exit_reason": None,
    }

    try:
        db.collection("stock_picks_archive").document(pick_id).set(snapshot)
        logger.info(f"Archived immutable snapshot: {pick_id} "
                     f"(score={snapshot['composite_score']:.1f}, "
                     f"price={snapshot.get('entry_price')})")
        return pick_id
    except Exception as e:
        logger.error(f"Failed to archive snapshot {pick_id}: {e}")
        return None


def archive_run_snapshots(
    db,
    actionable_leads: List[Dict],
    run_date: str,
) -> List[str]:
    """
    Archive all actionable leads from a single run.

    Args:
        db: Firestore client
        actionable_leads: List of candidate dicts from runner
        run_date: ISO date string

    Returns:
        List of archived pick_ids
    """
    archived_ids = []
    for candidate in actionable_leads:
        pick_id = archive_pick_snapshot(db, candidate, run_date)
        if pick_id:
            archived_ids.append(pick_id)
    logger.info(f"Archived {len(archived_ids)} pick snapshots for run {run_date}")
    return archived_ids


# =============================================================================
# FORWARD ALPHA AUDITOR (Design Doc Section 7.1, Loop A)
# =============================================================================

def compute_forward_alpha(
    db,
    max_lookback_days: int = 30,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Audit archived picks and update forward realized alpha at t+1, t+5, t+20.

    For each archived pick that has an entry_price, this function:
    1. Calculates days elapsed since the pick date
    2. Fetches current/historical price for the ticker
    3. Computes excess return vs SPY at each horizon
    4. Updates the archive document with realized alpha

    This should be called periodically (e.g., end of each trading day)
    by the manual run or an operator-triggered audit.

    Args:
        db: Firestore client
        max_lookback_days: Only process picks from the last N days
        dry_run: If True, compute but don't write to Firestore

    Returns:
        Summary dict with counts and any errors
    """
    if not db:
        return {"status": "error", "message": "No Firestore client"}

    summary = {
        "picks_evaluated": 0,
        "alphas_updated": 0,
        "errors": [],
        "results": [],
    }

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_lookback_days)).isoformat()
        archive_ref = db.collection("stock_picks_archive")

        # Query picks that still need alpha updates
        docs = archive_ref.where("run_date", ">=",
                                  (datetime.now(timezone.utc) - timedelta(days=max_lookback_days))
                                  .date().isoformat()).stream()

        for doc in docs:
            data = doc.to_dict()
            pick_id = data.get("pick_id", doc.id)
            entry_price = data.get("entry_price")
            ticker = data.get("ticker", data.get("symbol", ""))
            run_date_str = data.get("run_date", "")

            if not entry_price or not ticker or not run_date_str:
                continue

            summary["picks_evaluated"] += 1

            try:
                run_date = datetime.strptime(run_date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                days_elapsed = (datetime.now(timezone.utc) - run_date).days
            except (ValueError, TypeError):
                summary["errors"].append(f"{pick_id}: invalid run_date '{run_date_str}'")
                continue

            # Fetch current price and benchmark price
            try:
                ticker_price = _fetch_current_price(ticker)
                spy_price_at_entry = _fetch_historical_price("SPY", run_date_str)
                spy_price_now = _fetch_current_price("SPY")
            except Exception as price_err:
                summary["errors"].append(f"{pick_id}: price fetch failed: {price_err}")
                continue

            if not ticker_price or not spy_price_at_entry or not spy_price_now:
                continue

            # Calculate returns
            ticker_return = (ticker_price - entry_price) / entry_price
            spy_return = (spy_price_now - spy_price_at_entry) / spy_price_at_entry
            excess_return = ticker_return - spy_return

            # Determine which horizon(s) to update
            updates = {}
            forward_prices = data.get("forward_prices", {})
            forward_prices[f"t{days_elapsed}d"] = ticker_price

            for horizon in ALPHA_HORIZONS:
                field = f"alpha_t{horizon}"
                if days_elapsed >= horizon and data.get(field) is None:
                    updates[field] = round(excess_return, 6)

            if updates or forward_prices:
                updates["forward_prices"] = forward_prices
                updates["alpha_last_updated"] = datetime.now(timezone.utc).isoformat()

                if not dry_run:
                    archive_ref.document(pick_id).update(updates)

                summary["alphas_updated"] += 1
                summary["results"].append({
                    "pick_id": pick_id,
                    "ticker": ticker,
                    "days_elapsed": days_elapsed,
                    "ticker_return": round(ticker_return, 4),
                    "spy_return": round(spy_return, 4),
                    "excess_return": round(excess_return, 4),
                    "horizons_updated": list(updates.keys()),
                })

    except Exception as e:
        logger.error(f"Forward alpha computation failed: {e}")
        summary["errors"].append(f"Global error: {e}")

    logger.info(f"Forward alpha audit: evaluated {summary['picks_evaluated']} picks, "
                f"updated {summary['alphas_updated']} alphas, "
                f"{len(summary['errors'])} errors")
    return summary


# =============================================================================
# FACTOR IC ENGINE (Design Doc Section 7.1, IC Backpropagation)
# =============================================================================

def compute_factor_ic(
    db,
    lookback_days: int = IC_ROLLING_WINDOW_DAYS,
    alpha_horizon: int = 5,
) -> Dict[str, Any]:
    """
    Compute the Information Coefficient (IC) for each scoring factor.

    IC = rank correlation between factor scores and realized forward alpha.
    Uses Spearman rank correlation over a rolling window.

    From the design doc (Section 7.1):
      IC_i = ρ_rank(Factor_i, Alpha_{t+5d})
      - IC > +0.05 → boost factor weight by up to +20%
      - IC <= 0.00 → decay factor weight toward zero

    Args:
        db: Firestore client
        lookback_days: Rolling window for IC calculation
        alpha_horizon: Which forward alpha horizon to use (1, 5, or 20)

    Returns:
        Dict with factor ICs, recommended weight adjustments, and metadata
    """
    if not db:
        return {"status": "error", "message": "No Firestore client"}

    alpha_field = f"alpha_t{alpha_horizon}"
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()

    # Collect archived picks with completed alpha measurements
    archive_ref = db.collection("stock_picks_archive")
    docs = archive_ref.where("run_date", ">=", cutoff_date).stream()

    records = []
    for doc in docs:
        data = doc.to_dict()
        alpha = data.get(alpha_field)
        factor_scores = data.get("factor_scores", {})
        if alpha is not None and factor_scores:
            records.append({
                "alpha": alpha,
                "factors": factor_scores,
                "pick_id": data.get("pick_id", doc.id),
                "ticker": data.get("ticker", ""),
            })

    if len(records) < 5:
        logger.warning(f"Insufficient data for IC calculation: {len(records)} records "
                       f"(need ≥ 5). Skipping.")
        return {
            "status": "insufficient_data",
            "records_available": len(records),
            "minimum_required": 5,
            "factor_ics": {},
        }

    # Compute Spearman rank correlation for each factor
    factor_ics = {}
    weight_adjustments = {}

    for factor_name in TRACKED_FACTORS:
        factor_values = []
        alpha_values = []

        for rec in records:
            fv = rec["factors"].get(factor_name)
            if fv is not None:
                factor_values.append(fv)
                alpha_values.append(rec["alpha"])

        if len(factor_values) < 5:
            factor_ics[factor_name] = {
                "ic": None,
                "sample_size": len(factor_values),
                "status": "insufficient_data",
            }
            continue

        ic = _spearman_rank_correlation(factor_values, alpha_values)
        factor_ics[factor_name] = {
            "ic": round(ic, 4),
            "sample_size": len(factor_values),
            "status": "computed",
        }

        # Determine weight adjustment
        if ic > IC_BOOST_THRESHOLD:
            # Predictive factor → boost weight
            boost = min(MAX_WEIGHT_BOOST_PCT, (ic - IC_BOOST_THRESHOLD) * 2)
            weight_adjustments[factor_name] = {
                "direction": "BOOST",
                "magnitude": round(boost, 4),
                "reason": f"IC={ic:.4f} > {IC_BOOST_THRESHOLD} (predictive)",
            }
        elif ic <= IC_DECAY_THRESHOLD:
            # Non-predictive or negatively correlated → decay weight
            decay = min(MAX_WEIGHT_DECAY_PCT, abs(ic) * 2)
            weight_adjustments[factor_name] = {
                "direction": "DECAY",
                "magnitude": round(decay, 4),
                "reason": f"IC={ic:.4f} ≤ {IC_DECAY_THRESHOLD} (non-predictive)",
            }
        else:
            weight_adjustments[factor_name] = {
                "direction": "HOLD",
                "magnitude": 0.0,
                "reason": f"IC={ic:.4f} in neutral zone",
            }

    # Write factor ledger to Firestore
    ledger = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "alpha_horizon": alpha_horizon,
        "lookback_days": lookback_days,
        "sample_size": len(records),
        "factor_ics": factor_ics,
        "weight_adjustments": weight_adjustments,
    }

    try:
        db.collection("stockpicker_factor_ledger").document("current").set(ledger)
        logger.info(f"Factor IC ledger updated: {len(factor_ics)} factors computed "
                    f"from {len(records)} records")
    except Exception as e:
        logger.error(f"Failed to write factor IC ledger: {e}")

    return ledger


def load_weight_adjustments(db) -> Dict[str, float]:
    """
    Load the latest factor weight adjustments from Firestore.

    Returns a dict mapping factor_name -> multiplier (e.g., 1.15 = +15% boost).
    If no ledger exists or data is stale (> 7 days), returns neutral multipliers.
    """
    if not db:
        return {}

    try:
        doc = db.collection("stockpicker_factor_ledger").document("current").get()
        if not doc.exists:
            return {}

        data = doc.to_dict()
        computed_at = data.get("computed_at", "")
        try:
            computed_dt = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - computed_dt).days > 7:
                logger.info("Factor IC ledger is stale (> 7 days). Using neutral weights.")
                return {}
        except (ValueError, TypeError):
            return {}

        adjustments = data.get("weight_adjustments", {})
        multipliers = {}
        for factor_name, adj in adjustments.items():
            direction = adj.get("direction", "HOLD")
            magnitude = adj.get("magnitude", 0.0)
            if direction == "BOOST":
                multipliers[factor_name] = 1.0 + magnitude
            elif direction == "DECAY":
                multipliers[factor_name] = 1.0 - magnitude
            else:
                multipliers[factor_name] = 1.0

        return multipliers

    except Exception as e:
        logger.warning(f"Could not load weight adjustments: {e}")
        return {}


# =============================================================================
# EXECUTION FEEDBACK INGESTION (Design Doc Section 7.2, Loop B)
# =============================================================================

def ingest_execution_feedback(
    db,
    pick_id: str,
    realized_pnl: float,
    trade_duration_days: int,
    exit_reason: str,
    slippage: Optional[float] = None,
    execution_mode: str = "PAPER",
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Record trade execution feedback from tradingbot-bots for a specific pick.

    When a bot closes a position originally sourced from the StockPicker,
    it should call this function to backpropagate the realized P&L.

    This creates a record in stockpicker_execution_feedback/{pick_id}
    and links it back to the archive snapshot.

    Args:
        db: Firestore client
        pick_id: The archive pick ID (e.g., "2026-10-05_KTOS")
        realized_pnl: Net realized P&L in dollars
        trade_duration_days: How many trading days the position was held
        exit_reason: Why the trade was closed (e.g., "profit_target_hit",
                     "atr_stop_loss", "thesis_invalidation", "option_assignment")
        slippage: Optional execution slippage in dollars
        execution_mode: "PAPER" or "LIVE"
        metadata: Optional additional trade metadata

    Returns:
        Status dict
    """
    if not db:
        return {"status": "error", "message": "No Firestore client"}

    feedback = {
        "pick_id": pick_id,
        "realized_pnl": realized_pnl,
        "trade_duration_days": trade_duration_days,
        "exit_reason": exit_reason,
        "slippage": slippage,
        "execution_mode": execution_mode,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    }

    try:
        # Write execution feedback
        db.collection("stockpicker_execution_feedback").document(pick_id).set(feedback)

        # Link back to archive snapshot
        archive_ref = db.collection("stock_picks_archive").document(pick_id)
        archive_doc = archive_ref.get()
        if archive_doc.exists:
            archive_ref.update({
                "execution_linked": True,
                "realized_pnl": realized_pnl,
                "trade_duration_days": trade_duration_days,
                "exit_reason": exit_reason,
            })
            logger.info(f"Execution feedback linked to archive: {pick_id} "
                        f"(P&L=${realized_pnl:+.2f}, exit={exit_reason})")
        else:
            logger.warning(f"Archive snapshot not found for pick {pick_id} — "
                          f"feedback recorded but not linked")

        return {"status": "success", "pick_id": pick_id}

    except Exception as e:
        logger.error(f"Failed to ingest execution feedback for {pick_id}: {e}")
        return {"status": "error", "message": str(e)}


def compute_execution_summary(db, lookback_days: int = 90) -> Dict[str, Any]:
    """
    Compute aggregate execution statistics from feedback records.

    Returns summary of:
    - Overall win rate, average P&L, Sharpe approximation
    - Per-track (GROWTH vs INCOME) statistics
    - Per-exit-reason breakdown (identifies systematic weaknesses)
    - Sector-level stop-out rates (for self-healing screener adjustments)

    From design doc Section 7.2:
    - If picks in a sector experience >60% premature ATR stop-outs,
      the screener increases required margins for that sector.
    """
    if not db:
        return {"status": "error", "message": "No Firestore client"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    try:
        feedback_ref = db.collection("stockpicker_execution_feedback")
        docs = feedback_ref.where("recorded_at", ">=", cutoff).stream()

        all_records = []
        for doc in docs:
            data = doc.to_dict()
            all_records.append(data)

        if not all_records:
            return {
                "status": "no_data",
                "message": "No execution feedback records in the lookback window",
                "lookback_days": lookback_days,
            }

        # Overall statistics
        pnls = [r["realized_pnl"] for r in all_records if r.get("realized_pnl") is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        overall = {
            "total_trades": len(all_records),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
            "total_pnl": round(sum(pnls), 2) if pnls else 0,
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
            "avg_winner": round(statistics.mean(wins), 2) if wins else 0,
            "avg_loser": round(statistics.mean(losses), 2) if losses else 0,
            "avg_duration_days": round(
                statistics.mean([r["trade_duration_days"] for r in all_records
                                 if r.get("trade_duration_days") is not None]),
                1
            ) if any(r.get("trade_duration_days") is not None for r in all_records) else 0,
        }

        # Per exit-reason breakdown
        exit_reasons = {}
        for r in all_records:
            reason = r.get("exit_reason", "unknown")
            if reason not in exit_reasons:
                exit_reasons[reason] = {"count": 0, "pnls": []}
            exit_reasons[reason]["count"] += 1
            if r.get("realized_pnl") is not None:
                exit_reasons[reason]["pnls"].append(r["realized_pnl"])

        exit_breakdown = {}
        for reason, data in exit_reasons.items():
            exit_breakdown[reason] = {
                "count": data["count"],
                "avg_pnl": round(statistics.mean(data["pnls"]), 2) if data["pnls"] else 0,
                "pct_of_total": round(data["count"] / len(all_records), 4),
            }

        # Stop-out rate analysis (for self-healing)
        stop_out_count = sum(1 for r in all_records
                            if r.get("exit_reason") in ("atr_stop_loss", "stop_loss", "trailing_stop"))
        stop_out_rate = stop_out_count / len(all_records) if all_records else 0

        summary = {
            "status": "success",
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "overall": overall,
            "exit_breakdown": exit_breakdown,
            "stop_out_rate": round(stop_out_rate, 4),
            "self_healing_flags": [],
        }

        # Self-healing: flag if stop-out rate exceeds 60%
        if stop_out_rate > 0.60:
            summary["self_healing_flags"].append({
                "flag": "EXCESSIVE_STOP_OUTS",
                "rate": round(stop_out_rate, 4),
                "recommendation": "Increase required minimum gross margin and current ratio "
                                  "thresholds for affected sectors",
            })

        return summary

    except Exception as e:
        logger.error(f"Failed to compute execution summary: {e}")
        return {"status": "error", "message": str(e)}


# =============================================================================
# REJECTION PATTERN ANALYSIS (Design Doc Section 7.2, Loop A)
# =============================================================================

def analyze_rejection_patterns(db, lookback_days: int = 90) -> Dict[str, Any]:
    """
    Analyze operator rejection patterns to identify systematic screener weaknesses.

    From the design doc:
    - Ingest operator rejection patterns to auto-tune screener filter boundaries.
    - If a rejection reason dominates (>40% of rejections), the corresponding
      filter threshold should be tightened.

    Returns pattern analysis with recommended filter adjustments.
    """
    if not db:
        return {"status": "error", "message": "No Firestore client"}

    try:
        # Read current stock_picks/current for rejection history
        doc = db.collection("stock_picks").document("current").get()
        if not doc.exists:
            return {"status": "no_data"}

        data = doc.to_dict() or {}
        rejected = data.get("rejected_cooldown", [])

        if not rejected:
            return {
                "status": "no_rejections",
                "message": "No rejected picks in current state",
            }

        # Tally rejection reasons
        reason_counts = {}
        total = len(rejected)
        for r in rejected:
            reason = r.get("rejection_reason", "OPERATOR_DISCRETION")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        # Map rejection reasons to filter adjustment recommendations
        REASON_TO_FILTER = {
            "SPREAD_TOO_WIDE": {
                "filter": "bid_ask_spread_pct",
                "action": "tighten",
                "recommendation": "Lower max bid-ask spread threshold from 0.15% to 0.10%",
            },
            "TOO_VOLATILE": {
                "filter": "atr_pct_ceiling",
                "action": "tighten",
                "recommendation": "Lower ATR/Price ceiling from 3.5% to 2.5%",
            },
            "EARNINGS_TOO_CLOSE": {
                "filter": "min_days_to_earnings",
                "action": "increase",
                "recommendation": "Increase minimum days-to-earnings from 10 to 21",
            },
            "HIGH_DEBT": {
                "filter": "max_debt_to_equity",
                "action": "tighten",
                "recommendation": "Lower max Debt/Equity from 150% to 100%",
            },
            "OVER_ALLOCATED": {
                "filter": "max_per_sector",
                "action": "enforce",
                "recommendation": "Enforce stricter sector diversification (max 1 per sector)",
            },
        }

        patterns = {}
        recommended_adjustments = []
        for reason, count in reason_counts.items():
            pct = count / total
            patterns[reason] = {
                "count": count,
                "pct_of_total": round(pct, 4),
            }
            # Flag dominant rejection reasons
            if pct > 0.40 and reason in REASON_TO_FILTER:
                adj = REASON_TO_FILTER[reason].copy()
                adj["rejection_rate"] = round(pct, 4)
                adj["rejection_count"] = count
                recommended_adjustments.append(adj)

        analysis = {
            "status": "success",
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "total_rejections": total,
            "reason_patterns": patterns,
            "recommended_filter_adjustments": recommended_adjustments,
        }

        # Persist analysis
        try:
            db.collection("stockpicker_rejection_analysis").document("current").set(analysis)
        except Exception as e:
            logger.warning(f"Could not persist rejection analysis: {e}")

        return analysis

    except Exception as e:
        logger.error(f"Rejection pattern analysis failed: {e}")
        return {"status": "error", "message": str(e)}


# =============================================================================
# ARCHIVE CLEANUP / TTL (Design Doc Section 7 + User Requirement)
# =============================================================================

def cleanup_stale_archives(db, ttl_days: int = ARCHIVE_TTL_DAYS) -> Dict[str, Any]:
    """
    Purge archive documents older than the TTL to bound Firestore storage costs.

    Removes documents from:
    - stock_picks_archive/{pick_id} older than ttl_days
    - stock_picks/{date} daily dumps older than ttl_days
    - stockpicker_execution_feedback/{pick_id} older than ttl_days

    Args:
        db: Firestore client
        ttl_days: Delete records older than this many days

    Returns:
        Cleanup summary with counts of deleted documents
    """
    if not db:
        return {"status": "error", "message": "No Firestore client"}

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).date().isoformat()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    deleted = {"archive": 0, "daily_dumps": 0, "feedback": 0, "errors": []}

    # 1. Clean stock_picks_archive
    try:
        archive_ref = db.collection("stock_picks_archive")
        old_docs = archive_ref.where("run_date", "<", cutoff_date).limit(MAX_CLEANUP_BATCH).stream()
        for doc in old_docs:
            try:
                doc.reference.delete()
                deleted["archive"] += 1
            except Exception as e:
                deleted["errors"].append(f"archive/{doc.id}: {e}")
    except Exception as e:
        deleted["errors"].append(f"archive query failed: {e}")

    # 2. Clean stock_picks daily dumps (documents named by date)
    try:
        daily_ref = db.collection("stock_picks")
        # Daily dumps are named YYYY-MM-DD; skip 'current'
        docs = daily_ref.stream()
        for doc in docs:
            doc_id = doc.id
            if doc_id == "current":
                continue
            # Check if it looks like a date and is old
            try:
                doc_date = datetime.strptime(doc_id, "%Y-%m-%d").date()
                cutoff_dt = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).date()
                if doc_date < cutoff_dt:
                    doc.reference.delete()
                    deleted["daily_dumps"] += 1
            except ValueError:
                # Not a date-formatted document, skip
                continue
    except Exception as e:
        deleted["errors"].append(f"daily dumps cleanup failed: {e}")

    # 3. Clean stockpicker_execution_feedback
    try:
        feedback_ref = db.collection("stockpicker_execution_feedback")
        old_feedback = feedback_ref.where("recorded_at", "<", cutoff_iso).limit(MAX_CLEANUP_BATCH).stream()
        for doc in old_feedback:
            try:
                doc.reference.delete()
                deleted["feedback"] += 1
            except Exception as e:
                deleted["errors"].append(f"feedback/{doc.id}: {e}")
    except Exception as e:
        deleted["errors"].append(f"feedback query failed: {e}")

    total_deleted = deleted["archive"] + deleted["daily_dumps"] + deleted["feedback"]
    logger.info(f"Archive cleanup (TTL={ttl_days}d): deleted {total_deleted} documents "
                f"(archive={deleted['archive']}, daily={deleted['daily_dumps']}, "
                f"feedback={deleted['feedback']})")

    return {
        "status": "success",
        "ttl_days": ttl_days,
        "cutoff_date": cutoff_date,
        "deleted": deleted,
        "total_deleted": total_deleted,
    }


# =============================================================================
# COMPREHENSIVE FEEDBACK LOOP ORCHESTRATOR
# =============================================================================

def run_feedback_loop(
    db,
    include_alpha_audit: bool = True,
    include_factor_ic: bool = True,
    include_execution_summary: bool = True,
    include_rejection_analysis: bool = True,
    include_cleanup: bool = True,
) -> Dict[str, Any]:
    """
    Run the complete closed-loop feedback pipeline.

    This is the top-level orchestrator that runs all feedback components
    in sequence. It should be called after each StockPicker run or
    triggered independently as an audit.

    Steps:
    1. Forward Alpha Audit (update t+1/t+5/t+20 for recent picks)
    2. Factor IC Computation (rank-correlate factors with realized alpha)
    3. Execution Summary (aggregate bot trade P&L feedback)
    4. Rejection Pattern Analysis (identify systematic filter weaknesses)
    5. Archive Cleanup (purge records older than TTL)

    Returns:
        Comprehensive feedback loop results
    """
    logger.info("=" * 60)
    logger.info("Starting Closed-Loop Feedback Engine...")
    logger.info("=" * 60)

    results = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "components": {},
    }

    if include_alpha_audit:
        logger.info("Step 1/5: Forward Alpha Audit...")
        results["components"]["alpha_audit"] = compute_forward_alpha(db)

    if include_factor_ic:
        logger.info("Step 2/5: Factor IC Computation...")
        results["components"]["factor_ic"] = compute_factor_ic(db)

    if include_execution_summary:
        logger.info("Step 3/5: Execution Summary...")
        results["components"]["execution_summary"] = compute_execution_summary(db)

    if include_rejection_analysis:
        logger.info("Step 4/5: Rejection Pattern Analysis...")
        results["components"]["rejection_analysis"] = analyze_rejection_patterns(db)

    if include_cleanup:
        logger.info("Step 5/5: Archive Cleanup (TTL)...")
        results["components"]["cleanup"] = cleanup_stale_archives(db)

    logger.info("=" * 60)
    logger.info("✅ Closed-Loop Feedback Engine completed")
    logger.info("=" * 60)

    return results


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _extract_factor_scores(candidate: Dict) -> Dict[str, float]:
    """
    Extract individual factor sub-scores from a candidate for IC tracking.

    Parses the fundamental_reasons string and structured fields to recover
    individual factor contributions to the total score.
    """
    scores = {}

    # Direct numeric fields
    scores["explosiveness"] = candidate.get("explosiveness", 0.0)
    scores["revenue_yoy"] = candidate.get("revenue_yoy")
    scores["operating_margin"] = candidate.get("operating_margin")
    scores["gross_margin"] = candidate.get("gross_margin")
    scores["debt_to_equity"] = candidate.get("debt_to_equity")
    scores["eps_surprise"] = candidate.get("eps_surprise")
    scores["recommendation_mean"] = candidate.get("recommendation_mean")

    # Parse factor adjustments from fundamental_reasons string
    reasons_str = candidate.get("fundamental_reasons", "")
    if isinstance(reasons_str, str):
        # Extract numeric adjustments: "rel_vol=2.10x (+4.0)" -> relative_volume = 4.0
        import re
        patterns = {
            "relative_volume": r"rel_vol=[\d.]+x\s*\(([+-]?[\d.]+)\)",
            "price_momentum_1d": r"1d_price=[+-]?[\d.]+%\s*\(([+-]?[\d.]+)\)",
            "analyst_views": r"analyst=[\d.]+\s*\(([+-]?[\d.]+)\)",
            "growth_quality": r"growth_quality=[+-]?[\d.]+\s*\(([+-]?[\d.]+)\)",
            "persistence": r"persistence=\w+\s*\(([+-]?[\d.]+)\)",
            "insider_buys": r"insider.*\(([+-]?[\d.]+)\)",
            "congress_buys": r"congress.*\(([+-]?[\d.]+)\)",
        }
        for factor_name, pattern in patterns.items():
            match = re.search(pattern, reasons_str)
            if match:
                try:
                    scores[factor_name] = float(match.group(1))
                except (ValueError, TypeError):
                    pass

    # Remove None values
    return {k: v for k, v in scores.items() if v is not None}


def _spearman_rank_correlation(x: List[float], y: List[float]) -> float:
    """
    Compute Spearman rank correlation coefficient between two lists.

    Uses pure Python implementation (no scipy dependency required).
    """
    n = len(x)
    if n < 2:
        return 0.0

    # Compute ranks (average rank for ties)
    def _rank(values):
        sorted_pairs = sorted(enumerate(values), key=lambda p: p[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[sorted_pairs[k][0]] = avg_rank
            i = j + 1
        return ranks

    rank_x = _rank(x)
    rank_y = _rank(y)

    # Pearson correlation on ranks
    mean_rx = sum(rank_x) / n
    mean_ry = sum(rank_y) / n

    cov = sum((rx - mean_rx) * (ry - mean_ry) for rx, ry in zip(rank_x, rank_y))
    std_x = (sum((rx - mean_rx) ** 2 for rx in rank_x)) ** 0.5
    std_y = (sum((ry - mean_ry) ** 2 for ry in rank_y)) ** 0.5

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


def _safe_nested_get(d: Dict, *keys) -> Any:
    """Safely get a nested dict value."""
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _fetch_current_price(ticker: str) -> Optional[float]:
    """
    Fetch the current market price for a ticker via Yahoo Finance.

    Uses the same Yahoo Finance approach as core.py to avoid adding
    new dependencies.
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.debug(f"Could not fetch current price for {ticker}: {e}")
    return None


def _fetch_historical_price(ticker: str, date_str: str) -> Optional[float]:
    """
    Fetch the closing price for a ticker on a specific date.
    """
    try:
        import yfinance as yf
        from datetime import datetime as dt_cls

        target = dt_cls.strptime(date_str, "%Y-%m-%d")
        start = target - timedelta(days=5)  # Buffer for weekends/holidays
        end = target + timedelta(days=5)

        stock = yf.Ticker(ticker)
        hist = stock.history(start=start.strftime("%Y-%m-%d"),
                             end=end.strftime("%Y-%m-%d"))
        if not hist.empty:
            # Find closest date
            idx = hist.index.get_indexer([target], method="nearest")[0]
            return float(hist["Close"].iloc[idx])
    except Exception as e:
        logger.debug(f"Could not fetch historical price for {ticker} on {date_str}: {e}")
    return None
