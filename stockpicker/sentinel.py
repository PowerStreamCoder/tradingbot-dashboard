"""
In-Flight Thesis Sentinel & Downstream Adaptive Gateway (Phase 6).

Implements Section 8 of the QUIVERQUANT_STOCKPICKER_ENHANCEMENT_DESIGN.md:
- Pre-market position health audit for all active bot symbols (08:30 AM ET).
- Signal half-life monitoring and mid-trade thesis degradation detection (ΔS = S_today - S_entry).
- Multi-tier response gateway:
    Tier 1 (Mild Decay, ΔS in [-10, -25]): TIGHTEN_STOPS (ATR trailing floor 2.5x -> 1.2x)
    Tier 2 (Severe Drop, S < 50 or ΔS <= -30): THESIS_INVALIDATION_EXIT
    Tier 3 (Acute Shock, 8-K / regulatory crisis): EMERGENCY_EXIT
- Track 2 Covered Call Downstream Protection:
    Volatility Spike: ATR/Price > 3.5% -> VOLATILITY_LOCKOUT (no new short calls)
    Earnings Collision: Earnings <= 10 days -> EARLY_CALL_HARVEST (buy back short call)
- Publishes telemetry directly to Firestore:
    `bots/bot_{client_id}` and updates `stock_picks/current`
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
UTC = timezone.utc

# Feature Toggle: In-Flight Thesis Sentinel & Downstream Adaptive Gateway (Phase 6)
# Switched off by default per operator preference. Can be enabled via env var SENTINEL_ENABLED=true
# or at runtime via set_sentinel_enabled(True) / POST /api/stock-picks/sentinel/toggle.
_SENTINEL_ENABLED = os.environ.get("SENTINEL_ENABLED", "false").lower() in ("true", "1", "yes")

# Thesis-decay thresholds (design doc Section 8). Single source of truth so the
# code, the module docstring, and the emitted flag text cannot drift apart again.
# (Previously the flag text said 50.0 while the comparison used 45.0.)
SENTINEL_INVALIDATION_SCORE = 50.0    # S_today < 50   -> THESIS_INVALIDATION_EXIT
SENTINEL_MILD_DECAY_SCORE = 60.0      # S_today < 60   -> TIGHTEN_STOPS
SENTINEL_INVALIDATION_DELTA = -30.0   # ΔS <= -30      -> THESIS_INVALIDATION_EXIT
SENTINEL_MILD_DECAY_DELTA_LOW = -25.0  # ΔS within [-25, -10] -> TIGHTEN_STOPS
SENTINEL_MILD_DECAY_DELTA_HIGH = -10.0
SENTINEL_STOP_TIGHTENING_FACTOR = 0.5  # 50% tighter ATR floor on mild decay


def is_sentinel_enabled() -> bool:
    """Check if the In-Flight Thesis Sentinel feature is currently enabled."""
    return _SENTINEL_ENABLED


def set_sentinel_enabled(enabled: bool) -> None:
    """Enable or disable the In-Flight Thesis Sentinel feature at runtime."""
    global _SENTINEL_ENABLED
    _SENTINEL_ENABLED = bool(enabled)
    logger.info(f"[SENTINEL] Feature state set to: {'ENABLED' if _SENTINEL_ENABLED else 'DISABLED'}")


def audit_in_flight_positions(db=None, force: bool = False) -> Dict[str, Any]:
    """
    Audit active trading bot positions for mid-trade thesis deterioration.

    Compares the current quantitative score (S_today) against the score at entry
    (S_entry) and generates action instructions for downstream trading bots.

    Args:
        db: Firestore client (optional, initialized if None)
        force: If True, execute audit regardless of feature flag state.

    Returns:
        Summary dict containing audited positions, action counts, and telemetry.
    """
    now_dt = datetime.now(UTC)
    now_iso = now_dt.isoformat()

    if not is_sentinel_enabled() and not force:
        logger.info("[SENTINEL] In-Flight Thesis Sentinel is currently DISABLED. Skipping audit.")
        return {
            "status": "disabled",
            "enabled": False,
            "message": "In-Flight Thesis Sentinel & Downstream Adaptive Gateway is currently switched off.",
            "audited_at": now_iso,
            "positions_checked": 0,
            "actions_summary": {
                "HOLD": 0,
                "TIGHTEN_STOPS": 0,
                "THESIS_INVALIDATION_EXIT": 0,
                "VOLATILITY_LOCKOUT": 0,
                "EARLY_CALL_HARVEST": 0,
                "EMERGENCY_EXIT": 0,
            },
            "results": [],
            "degraded": [],
        }

    summary = {
        "status": "success",
        "enabled": True,
        "audited_at": now_iso,
        "positions_checked": 0,
        "actions_summary": {
            "HOLD": 0,
            "TIGHTEN_STOPS": 0,
            "THESIS_INVALIDATION_EXIT": 0,
            "VOLATILITY_LOCKOUT": 0,
            "EARLY_CALL_HARVEST": 0,
            "EMERGENCY_EXIT": 0,
        },
        "results": [],
        "degraded": [],
    }

    # 1. Hydrate active bot positions
    active_positions = _hydrate_active_positions(db)
    if not active_positions:
        logger.info("[SENTINEL] No active bot positions to audit.")
        return summary

    from stockpicker.core import compute_fundamental_score
    from stockpicker.income_screener import evaluate_covered_call_candidate

    for pos in active_positions:
        symbol = pos.get("symbol", "").upper().strip()
        client_id = pos.get("client_id")
        bot_id = pos.get("bot_id") or f"{symbol.lower()}_sma"
        strategy_track = (pos.get("strategy_track") or "GROWTH").upper()
        entry_score = pos.get("entry_score")
        if entry_score is None:
            entry_score = pos.get("initial_score")

        if not symbol:
            continue

        # ΔS requires a genuine entry score. There is no legitimate default, so
        # instead of scoring against a fabricated baseline (the old 75.0) we
        # skip the position and surface it for the operator.
        if entry_score is None:
            summary["degraded"].append({
                "symbol": symbol,
                "reason": "no initial_score on record - ΔS baseline unknown",
            })
            logger.warning(f"[SENTINEL] Skipping {symbol}: entry score unknown; cannot compute ΔS")
            continue
        entry_score = float(entry_score)

        summary["positions_checked"] += 1
        flags: List[str] = []
        action = "HOLD"
        thesis_state = "HEALTHY"
        recommended_stop_tightening = 1.0

        # 2. Score symbol today (S_today)
        try:
            fund_data = compute_fundamental_score(symbol)
            today_score = float(fund_data.get("score", entry_score))
        except Exception as e:
            logger.warning(f"[SENTINEL] Could not compute fresh score for {symbol}: {e}")
            today_score = entry_score

        delta_s = round(today_score - entry_score, 2)

        # 3. Track 1 & General Equity Evaluation: Thesis Decay Tiers
        if today_score < SENTINEL_INVALIDATION_SCORE or delta_s <= SENTINEL_INVALIDATION_DELTA:
            thesis_state = "INVALIDATED"
            action = "THESIS_INVALIDATION_EXIT"
            flags.append(
                f"Score collapsed by {delta_s} pts to {today_score:.1f} "
                f"(below invalidation threshold {SENTINEL_INVALIDATION_SCORE:.0f})")
        elif (SENTINEL_MILD_DECAY_DELTA_LOW <= delta_s <= SENTINEL_MILD_DECAY_DELTA_HIGH
              or today_score < SENTINEL_MILD_DECAY_SCORE):
            thesis_state = "MILD_DECAY"
            action = "TIGHTEN_STOPS"
            recommended_stop_tightening = SENTINEL_STOP_TIGHTENING_FACTOR
            flags.append(f"Mild fundamental momentum decay: ΔS={delta_s:+.1f} pts")
        else:
            thesis_state = "HEALTHY"
            action = "HOLD"

        # 4. Track 2: Covered Call Volatility & Earnings Collisions
        if strategy_track == "INCOME":
            try:
                inc_data = evaluate_covered_call_candidate(symbol)
                if inc_data:
                    days_to_earnings = inc_data.get("days_to_earnings")
                    if days_to_earnings is not None and days_to_earnings <= 10:
                        thesis_state = "EARNINGS_HAZARD"
                        action = "EARLY_CALL_HARVEST"
                        flags.append(f"Impending earnings release in {days_to_earnings} days (<=10d collision window)")

                    # The income screener reports ATR under "est_atr_pct" in
                    # percent units (e.g. 4.2 == 4.2%). The previous read of
                    # "atr_pct" (a key that is never populated) always yielded
                    # 0.0, silently disabling the volatility lockout.
                    atr_pct = inc_data.get("est_atr_pct", 0.0)
                    if atr_pct > 3.5:
                        thesis_state = "VOLATILITY_EXPANSION"
                        if action == "HOLD":
                            action = "VOLATILITY_LOCKOUT"
                        flags.append(f"Daily ATR expanded to {atr_pct:.1f}% (exceeds 3.5% income ceiling)")
            except Exception as e:
                logger.warning(f"[SENTINEL] Options suitability check failed for {symbol}: {e}")

        # Update summary counters
        summary["actions_summary"][action] = summary["actions_summary"].get(action, 0) + 1

        telemetry = {
            "last_evaluated": now_iso,
            "entry_score": entry_score,
            "current_score": today_score,
            "score_delta": delta_s,
            "thesis_state": thesis_state,
            "invalidation_flags": flags,
            "action_instruction": action,
            "recommended_stop_tightening": recommended_stop_tightening,
        }

        pos_result = {
            "symbol": symbol,
            "client_id": client_id,
            "bot_id": bot_id,
            "strategy_track": strategy_track,
            "thesis_telemetry": telemetry,
        }
        summary["results"].append(pos_result)

        # 5a. Persist per-bot telemetry (independent documents - no race).
        if db:
            _persist_bot_telemetry(db, pos_result)

    # 5b. Mirror all telemetry into stock_picks/current with a single write.
    if db and summary["results"]:
        _persist_current_telemetry(db, summary["results"])

    logger.info(f"[SENTINEL] Audit complete: checked {summary['positions_checked']} positions, "
                f"actions: {summary['actions_summary']}")
    return summary


def _hydrate_active_positions(db) -> List[Dict[str, Any]]:
    """Load active bot positions from bots.json and/or Firestore."""
    positions = []
    seen_symbols = set()

    # Priority 1: Check bots.json config
    bots_config_path = _find_bots_config()
    if bots_config_path and os.path.exists(bots_config_path):
        try:
            with open(bots_config_path, "r") as f:
                cfg = json.load(f)
            for b in cfg.get("bots", []):
                sym = b.get("symbol", "").upper().strip()
                if sym and sym not in seen_symbols:
                    seen_symbols.add(sym)
                    positions.append({
                        "symbol": sym,
                        "client_id": b.get("client_id"),
                        "bot_id": b.get("name"),
                        "strategy_track": b.get("strategy_track"),
                        "entry_score": b.get("initial_score"),
                    })
        except Exception as e:
            logger.warning(f"[SENTINEL] Could not read bots config at {bots_config_path}: {e}")

    # Priority 2: Augment from Firestore stock_picks/current already_accepted
    if db:
        try:
            doc = db.collection("stock_picks").document("current").get()
            if doc.exists:
                data = doc.to_dict() or {}
                for acc in data.get("already_accepted", []):
                    sym = (acc.get("symbol") or acc.get("ticker") or "").upper().strip()
                    if sym and sym not in seen_symbols:
                        seen_symbols.add(sym)
                        positions.append({
                            "symbol": sym,
                            "client_id": acc.get("client_id"),
                            "bot_id": acc.get("bot_id"),
                            "strategy_track": acc.get("strategy_track") or acc.get("track"),
                            "entry_score": (acc.get("initial_score")
                                            or acc.get("composite_score")
                                            or acc.get("fundamental_score")
                                            or acc.get("score")),
                        })
        except Exception as e:
            logger.warning(f"[SENTINEL] Could not read already_accepted from Firestore: {e}")

    return positions


def _persist_telemetry(db, pos_result: Dict[str, Any]):
    """Persist telemetry for a single position (back-compat wrapper).

    Real callers use the split helpers so the `stock_picks/current` write is
    batched once per audit instead of once per position.
    """
    _persist_bot_telemetry(db, pos_result)
    _persist_current_telemetry(db, [pos_result])


def _persist_bot_telemetry(db, pos_result: Dict[str, Any]):
    """Write thesis telemetry to `bots/bot_{client_id}` for bot consumption."""
    symbol = pos_result["symbol"]
    client_id = pos_result.get("client_id")
    telemetry = pos_result["thesis_telemetry"]

    if client_id is None:
        return

    bot_doc_id = f"bot_{client_id}"
    try:
        db.collection("bots").document(bot_doc_id).set({
            "client_id": client_id,
            "symbol": symbol,
            "thesis_telemetry": telemetry,
        }, merge=True)
    except Exception as e:
        logger.warning(f"[SENTINEL] Failed to write telemetry to bots/{bot_doc_id}: {e}")


def _persist_current_telemetry(db, pos_results: List[Dict[str, Any]]):
    """Mirror sentinel telemetry into `stock_picks/current` in ONE write.

    Batching matters: the previous per-position implementation re-read and
    rewrote the entire `already_accepted` array N times per audit, so
    concurrent writers (accept/reject/provision) could clobber each other and
    a single audit amplified writes linearly with the bot count. The write
    additionally goes through `run_stock_pick_rmw`, so in production it runs
    inside a Firestore transaction and cannot clobber a concurrent HITL
    transition.
    """
    if not pos_results:
        return

    try:
        from stockpicker.firestore_utils import run_stock_pick_rmw

        updates_by_symbol = {}
        for pr in pos_results:
            sym = (pr.get("symbol") or "").upper().strip()
            telemetry = pr.get("thesis_telemetry") or {}
            if not sym:
                continue
            score_delta = telemetry.get("score_delta")
            updates_by_symbol[sym] = {
                "current_score": telemetry.get("current_score"),
                "score_delta": (f"{score_delta:+.1f}" if score_delta is not None else None),
                "thesis_state": telemetry.get("thesis_state"),
                "action_instruction": telemetry.get("action_instruction"),
                "last_sentinel_audit": telemetry.get("last_evaluated"),
            }

        if not updates_by_symbol:
            return

        current_ref = db.collection("stock_picks").document("current")

        def _mutate(data):
            already_accepted = data.get("already_accepted", [])
            if not isinstance(already_accepted, list):
                return False
            changed = False
            for item in already_accepted:
                if not isinstance(item, dict):
                    continue
                sym = (item.get("symbol") or item.get("ticker") or "").upper()
                patch = updates_by_symbol.get(sym)
                if not patch:
                    continue
                for key, value in patch.items():
                    if value is not None:
                        item[key] = value
                changed = True
            return changed

        run_stock_pick_rmw(_mutate, doc_ref=current_ref, db=db)
    except Exception as e:
        logger.warning(f"[SENTINEL] Failed to update stock_picks/current telemetry: {e}")


def _find_bots_config() -> Optional[str]:
    """Find bots.json in known paths."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tradingbot-config", "bots.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "bots.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None
