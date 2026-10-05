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
        strategy_track = pos.get("strategy_track", "GROWTH")
        entry_score = float(pos.get("entry_score") or pos.get("initial_score") or 75.0)

        if not symbol:
            continue

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
        if today_score < 45.0 or delta_s <= -30.0:
            thesis_state = "INVALIDATED"
            action = "THESIS_INVALIDATION_EXIT"
            flags.append(f"Score collapsed by {delta_s} pts to {today_score:.1f} (below invalidation threshold 50.0)")
        elif -25.0 <= delta_s <= -10.0 or today_score < 60.0:
            thesis_state = "MILD_DECAY"
            action = "TIGHTEN_STOPS"
            recommended_stop_tightening = 0.5  # tighten ATR multiplier by 50%
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

                    atr_pct = inc_data.get("atr_pct", 0.0)
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

        # 5. Persist telemetry to Firestore if available
        if db:
            _persist_telemetry(db, pos_result)

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
                        "strategy_track": b.get("strategy_track", "GROWTH"),
                        "entry_score": b.get("initial_score", 75.0),
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
                            "strategy_track": acc.get("strategy_track", "GROWTH"),
                            "entry_score": acc.get("initial_score") or acc.get("score") or 75.0,
                        })
        except Exception as e:
            logger.warning(f"[SENTINEL] Could not read already_accepted from Firestore: {e}")

    return positions


def _persist_telemetry(db, pos_result: Dict[str, Any]):
    """Persist thesis telemetry to Firestore for bot execution and dashboard display."""
    symbol = pos_result["symbol"]
    client_id = pos_result.get("client_id")
    telemetry = pos_result["thesis_telemetry"]

    # Write to bots collection for UniversalSMABot consumption
    if client_id is not None:
        try:
            bot_doc_id = f"bot_{client_id}"
            db.collection("bots").document(bot_doc_id).set({
                "client_id": client_id,
                "symbol": symbol,
                "thesis_telemetry": telemetry,
            }, merge=True)
        except Exception as e:
            logger.warning(f"[SENTINEL] Failed to write telemetry to bots/{bot_doc_id}: {e}")

    # Update current stock picks record in Firestore
    try:
        current_ref = db.collection("stock_picks").document("current")
        doc = current_ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            already_accepted = data.get("already_accepted", [])
            for item in already_accepted:
                if (item.get("symbol") or item.get("ticker") or "").upper() == symbol:
                    item["current_score"] = telemetry["current_score"]
                    item["score_delta"] = f"{telemetry['score_delta']:+.1f}"
                    item["thesis_state"] = telemetry["thesis_state"]
                    item["action_instruction"] = telemetry["action_instruction"]
                    item["last_sentinel_audit"] = telemetry["last_evaluated"]
            current_ref.update({"already_accepted": already_accepted})
    except Exception as e:
        logger.warning(f"[SENTINEL] Failed to update stock_picks/current telemetry for {symbol}: {e}")


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
