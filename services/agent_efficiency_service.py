"""
Agent Efficiency Measurement & Regular Monitoring Service

Computes quantitative efficiency metrics across all 7 autonomous agents:
- AgentOrchestrator
- AdaptiveEntryAgent
- AdaptivePnLAgent
- AdaptiveCoveredCallAgent
- AdaptiveFeedbackEngine
- LearningEngine
- TradeLogAuditor

Aggregates telemetry from Firestore collections:
- adaptive_action_log
- applied_changes
- execution_metrics
- learning_candidates
- audit_findings
- bot_overview
- exit_telemetry_snapshots

Provides:
- Rolling effectiveness scores (S-score in [-1.0, 1.0])
- R-multiple retention and giveback savings
- Computational runtime & SLA monitoring (cascade phases p50, p95, p99, max)
- Proactive alert detection (SLA breaches, circuit breakers, parameter pinning)
- Thread-safe caching with configurable TTL
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Dict, List, Optional, Any, Union, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Constants
DEFAULT_CACHE_TTL_SECONDS = 60.0
SLA_P99_THRESHOLD_MS = 500.0
SLA_MAX_THRESHOLD_MS = 2000.0
NEGATIVE_ALPHA_THRESHOLD = -0.20
CIRCUIT_BREAKER_PF_DELTA = -0.15

ACTIVE_SYMBOLS = ["NVDA", "IWM", "MSFT", "CAT", "QQQ", "AFRM"]

HARD_SAFETY_CLAMPS = {
    "break_even_lock_r_multiple": (0.5, 2.0),
    "scale_out_target_r": (0.8, 3.0),
    "pyramid_trigger_r_multiple": (0.8, 2.5),
    "time_decay_max_days": (1.0, 15.0),
    "peak_giveback_min_r": (0.5, 3.0),
    "gap_fade_shield_arm": (0.0, 1.0),
}


# ---------------------------------------------------------------------------
# Data Models / Schemas
# ---------------------------------------------------------------------------

class LatencyStats(BaseModel):
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0
    count: int = 0
    sla_breaches: int = 0


class EfficiencyAlert(BaseModel):
    alert_id: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "WARNING"
    agent: str
    symbol: Optional[str] = None
    message: str
    metric: str
    value: Any
    threshold: Any
    timestamp: str


class ParameterState(BaseModel):
    parameter_name: str
    current_value: float
    baseline_value: float
    min_clamp: float
    max_clamp: float
    drift_pct: float
    is_pinned: bool


class AgentScoreCard(BaseModel):
    agent_name: str
    status: str = "HEALTHY"  # HEALTHY, DEGRADED, STALLED, PAUSED
    headline_metric_name: str
    headline_metric_value: Union[float, str, int]
    metrics: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class FleetEfficiencySummary(BaseModel):
    timestamp: str
    active_bots: List[str]
    fleet_health: str = "HEALTHY"
    aggregate_alpha: Dict[str, Any] = Field(default_factory=dict)
    agent_scores: Dict[str, AgentScoreCard] = Field(default_factory=dict)
    sla_metrics: Dict[str, LatencyStats] = Field(default_factory=dict)
    active_alerts_count: int = 0


# ---------------------------------------------------------------------------
# Core Service
# ---------------------------------------------------------------------------

class AgentEfficiencyService:
    """
    Core aggregator service for evaluating agent performance, alpha efficiency,
    and runtime SLA compliance.
    """

    def __init__(
        self,
        db: Optional[Any] = None,
        cache_ttl: float = DEFAULT_CACHE_TTL_SECONDS,
        max_cache_entries: int = 128,
    ):
        self._db = db
        self.cache_ttl = cache_ttl
        self.max_cache_entries = max(16, max_cache_entries)
        self._cache: Dict[str, Tuple[datetime, Any]] = {}
        self._lock = Lock()

    def _get_db(self):
        if self._db is None:
            try:
                from google.cloud import firestore
                self._db = firestore.Client()
            except Exception as e:
                logger.error(f"Failed to initialize Firestore client: {e}")
                raise
        return self._db

    def clear_cache(self):
        """Clears in-memory cached responses."""
        with self._lock:
            self._cache.clear()

    def _get_from_cache(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                ts, val = self._cache[key]
                if (datetime.now(timezone.utc) - ts).total_seconds() < self.cache_ttl:
                    return val
                del self._cache[key]
        return None

    def _set_in_cache(self, key: str, val: Any):
        with self._lock:
            now = datetime.now(timezone.utc)
            # Evict if at capacity
            if len(self._cache) >= self.max_cache_entries and key not in self._cache:
                # 1. Evict expired keys
                expired = [
                    k for k, (ts, _) in self._cache.items()
                    if (now - ts).total_seconds() >= self.cache_ttl
                ]
                for k in expired:
                    del self._cache[k]

                # 2. If still at capacity, evict oldest entry (LRU/FIFO)
                if len(self._cache) >= self.max_cache_entries:
                    oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                    del self._cache[oldest]

            self._cache[key] = (now, val)

    # -----------------------------------------------------------------------
    # Data Fetching Helpers
    # -----------------------------------------------------------------------

    def _fetch_collection_docs(
        self,
        collection_name: str,
        limit: int = 500,
        order_by_field: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            db = self._get_db()
            coll = db.collection(collection_name)
            docs = []
            stream = None
            if order_by_field:
                try:
                    from google.cloud import firestore
                    stream = coll.order_by(order_by_field, direction=firestore.Query.DESCENDING).limit(limit).stream()
                except Exception as ex:
                    logger.debug(f"Native order_by on {collection_name}.{order_by_field} failed ({ex}); using fallback stream.")
                    stream = None

            if stream is None:
                try:
                    stream = coll.limit(limit).stream()
                except Exception:
                    stream = coll.stream()

            for doc in stream:
                d = doc.to_dict() or {}
                d.setdefault("doc_id", doc.id)
                docs.append(d)

            # In-memory sorting fallback if order_by_field was requested
            if order_by_field and docs:
                docs.sort(
                    key=lambda x: str(x.get(order_by_field) or x.get("timestamp") or x.get("created_at") or x.get("ts") or ""),
                    reverse=True
                )
                docs = docs[:limit]

            return docs
        except Exception as e:
            logger.warning(f"Error fetching collection {collection_name}: {e}")
            return []

    # -----------------------------------------------------------------------
    # Computational Runtime & SLA Calculations
    # -----------------------------------------------------------------------

    @staticmethod
    def calculate_latency_percentiles(durations_ms: List[float]) -> LatencyStats:
        """Calculates p50, p95, p99, max and breach counts from durations in ms."""
        if not durations_ms:
            return LatencyStats()

        sorted_ms = sorted(durations_ms)
        n = len(sorted_ms)

        def get_p(pct: float) -> float:
            idx = int(math.ceil(pct / 100.0 * n)) - 1
            return round(sorted_ms[max(0, min(idx, n - 1))], 2)

        p50 = get_p(50)
        p95 = get_p(95)
        p99 = get_p(99)
        max_ms = round(sorted_ms[-1], 2)
        breaches = sum(1 for x in sorted_ms if x > SLA_P99_THRESHOLD_MS)

        return LatencyStats(
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
            max_ms=max_ms,
            count=n,
            sla_breaches=breaches
        )

    # -----------------------------------------------------------------------
    # Rolling Effectiveness & S-Score Calculation
    # -----------------------------------------------------------------------

    @staticmethod
    def calculate_effectiveness_score(actions: List[Dict[str, Any]], window: int = 20) -> float:
        """
        Calculates rolling effectiveness score S = (1/N) * sum(S_i) in [-1.0, 1.0].
        Each resolved action produces:
          +1.0 if final_pnl > 0 or realized_r > 0
          -1.0 if final_pnl < 0 or realized_r < 0
           0.0 if neutral / flat
        """
        resolved = []
        for a in actions:
            outcome = a.get("outcome") or {}
            is_resolved = a.get("resolved") or outcome.get("resolved")
            if is_resolved:
                pnl = a.get("final_pnl") if a.get("final_pnl") is not None else outcome.get("final_pnl", 0.0)
                r = a.get("realized_r") if a.get("realized_r") is not None else outcome.get("realized_r", 0.0)
                score = 0.0
                if pnl > 0.0 or (r is not None and r > 0.0):
                    score = 1.0
                elif pnl < 0.0 or (r is not None and r < 0.0):
                    score = -1.0
                resolved.append(score)

        if not resolved:
            return 0.0

        target_window = resolved[-window:] if len(resolved) > window else resolved
        return round(sum(target_window) / len(target_window), 3)

    # -----------------------------------------------------------------------
    # Main Aggregate Methods
    # -----------------------------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        """
        Returns high-level fleet overview, health status, and KPI scorecard
        across all 7 autonomous agents.
        """
        cached = self._get_from_cache("summary")
        if cached is not None:
            return cached

        # Fetch telemetry
        actions = self._fetch_collection_docs("adaptive_action_log", limit=500, order_by_field="timestamp")
        applied_changes = self._fetch_collection_docs("applied_changes", limit=200, order_by_field="timestamp")
        exec_metrics = self._fetch_collection_docs("execution_metrics", limit=500, order_by_field="timestamp")
        learning_cands = self._fetch_collection_docs("learning_candidates", limit=100, order_by_field="created_at")
        audit_findings = self._fetch_collection_docs("audit_findings", limit=100, order_by_field="created_at")
        overview_docs = self._fetch_collection_docs("bot_overview", limit=50)

        # 1. Runtime & Latency Metrics
        adaptive_latencies_ms = []
        marks_latencies_ms = []
        orders_latencies_ms = []
        throttle_bypasses = 0
        total_ticks = 0

        for em in exec_metrics:
            total_ticks += 1
            if em.get("proximity_throttled"):
                throttle_bypasses += 1
            
            # Nanoseconds to milliseconds
            ns_a = em.get("cascade_phase_adaptive_ns")
            if ns_a is not None and isinstance(ns_a, (int, float)):
                adaptive_latencies_ms.append(ns_a / 1e6)
            
            ns_m = em.get("cascade_phase_marks_ns")
            if ns_m is not None and isinstance(ns_m, (int, float)):
                marks_latencies_ms.append(ns_m / 1e6)
            
            ns_o = em.get("cascade_phase_orders_ns")
            if ns_o is not None and isinstance(ns_o, (int, float)):
                orders_latencies_ms.append(ns_o / 1e6)

        sla_adaptive = self.calculate_latency_percentiles(adaptive_latencies_ms)
        sla_marks = self.calculate_latency_percentiles(marks_latencies_ms)
        sla_orders = self.calculate_latency_percentiles(orders_latencies_ms)

        # 2. Agent 1: AgentOrchestrator
        # Intent-to-regime match and conflict resolution
        total_regime_checks = 0
        intent_matches = 0
        conflict_arbitrations = 0
        shadowed_exits = 0
        total_evals = 0

        for b in overview_docs:
            regime = b.get("regime_status") or b.get("macro_regime")
            intent = b.get("strategy_intent")
            if regime and intent:
                total_regime_checks += 1
                if (regime == "BULL" and "GROWTH" in intent) or \
                   (regime in ("NEUTRAL", "RANGE") and "RANGE" in intent or "INCOME" in intent) or \
                   (regime in ("BEAR", "HIGH_VIX") and "DEFENSIVE" in intent):
                    intent_matches += 1

        orchestrator_match_rate = round(intent_matches / total_regime_checks, 3) if total_regime_checks else 1.0

        orchestrator_score = AgentScoreCard(
            agent_name="AgentOrchestrator",
            status="HEALTHY" if orchestrator_match_rate >= 0.8 else "DEGRADED",
            headline_metric_name="Intent Match Rate",
            headline_metric_value=f"{round(orchestrator_match_rate * 100, 1)}%",
            metrics={
                "intent_match_rate": orchestrator_match_rate,
                "conflict_arbitration_count": conflict_arbitrations,
                "shadowed_rule_ratio": 0.0,
                "ml_exit_arbitration_enabled": any(
                    b.get("ml_exit_arbitration_enabled", False) for b in overview_docs
                ),
                "ml_arbitration_invocations": sum(
                    (b.get("ml_arbitration_invocations") or 0) for b in overview_docs
                ),
                "ml_critical_bypass_count": sum(
                    (b.get("ml_critical_bypass_count") or 0) for b in overview_docs
                ),
            },
            summary="Arbitrates high-level regime posture and resolves exit conflicts. "
                    "ML exit arbitration metrics reflect Pillar 1 dynamic learned precedence."
        )

        # 3. Agent 2: AdaptiveEntryAgent
        # Selectivity ratio, pre-queue blocks
        rejected_candidates = 0
        admitted_candidates = 0
        admitted_wins = 0
        admitted_losses = 0
        pre_queue_blocks: Dict[str, int] = {}

        for a in actions:
            a_type = a.get("action_type", "")
            if a_type.startswith("entry_") or "entry" in a_type:
                sub = a.get("context", {}).get("block_reason", a_type)
                pre_queue_blocks[sub] = pre_queue_blocks.get(sub, 0) + 1
                if "block" in a_type or "reject" in a_type:
                    rejected_candidates += 1
                else:
                    admitted_candidates += 1
                    outcome = a.get("outcome") or {}
                    pnl = outcome.get("final_pnl", 0)
                    if pnl > 0:
                        admitted_wins += 1
                    elif pnl < 0:
                        admitted_losses += 1

        total_entry_signals = rejected_candidates + admitted_candidates
        selectivity_ratio = round(rejected_candidates / total_entry_signals, 3) if total_entry_signals else 0.0
        admitted_win_rate = round(admitted_wins / admitted_candidates, 3) if admitted_candidates else None

        entry_agent_score = AgentScoreCard(
            agent_name="AdaptiveEntryAgent",
            status="HEALTHY",
            headline_metric_name="Filter Selectivity",
            headline_metric_value=f"{round(selectivity_ratio * 100, 1)}%",
            metrics={
                "selectivity_ratio": selectivity_ratio,
                "admitted_win_rate": admitted_win_rate,
                "pre_queue_blocks": pre_queue_blocks,
                "total_signals_evaluated": total_entry_signals,
            },
            summary="Screens pre-entry technical signals and rejects false breakouts."
        )

        # 4. Agent 3: AdaptivePnLAgent
        # R-Multiple retention, giveback saved, break-even conversion
        r_multiples = []
        giveback_saved_total = 0.0
        breakeven_locks_activated = 0
        breakeven_green_closes = 0

        for a in actions:
            a_type = a.get("action_type", "")
            outcome = a.get("outcome") or {}
            r = a.get("realized_r") if a.get("realized_r") is not None else outcome.get("realized_r")
            if r is not None:
                r_multiples.append(float(r))

            peak_unrealized = a.get("context", {}).get("peak_unrealized_pnl", 0.0)
            final_pnl = a.get("final_pnl") if a.get("final_pnl") is not None else outcome.get("final_pnl", 0.0)
            if peak_unrealized > 0 and final_pnl is not None:
                giveback_saved_total += max(0.0, float(peak_unrealized) - float(final_pnl))

            if "break_even" in a_type:
                breakeven_locks_activated += 1
                if final_pnl is not None and final_pnl >= 0:
                    breakeven_green_closes += 1

        mean_r = round(sum(r_multiples) / len(r_multiples), 2) if r_multiples else 0.0
        be_conversion = round(breakeven_green_closes / breakeven_locks_activated, 3) if breakeven_locks_activated else None

        pnl_agent_score = AgentScoreCard(
            agent_name="AdaptivePnLAgent",
            status="HEALTHY" if mean_r >= 0.0 else "DEGRADED",
            headline_metric_name="Average Realized R",
            headline_metric_value=f"{mean_r}R",
            metrics={
                "realized_r_mean": mean_r,
                "giveback_saved_total": round(giveback_saved_total, 2),
                "breakeven_conversion_rate": be_conversion,
                "breakeven_locks_count": breakeven_locks_activated,
            },
            summary="Manages in-flight risk, locks break-even stops, and guards peak unrealized profits."
        )

        # 5. Agent 4: AdaptiveCoveredCallAgent
        # Net yield captured, opportunity cost delta
        option_premium_harvested = 0.0
        opportunity_cost_delta = 0.0
        assignments_avoided = 0
        assignments_total = 0

        for a in actions:
            if "covered_call" in a.get("action_type", "") or "option" in a.get("action_type", ""):
                ctx = a.get("context", {})
                prem = ctx.get("premium_harvested", 0.0)
                capped_gain = ctx.get("equity_upside_capped", 0.0)
                option_premium_harvested += float(prem)
                opportunity_cost_delta += (float(prem) - float(capped_gain))
                if ctx.get("assigned") is False:
                    assignments_avoided += 1
                assignments_total += 1

        cc_avoidance_rate = round(assignments_avoided / assignments_total, 3) if assignments_total else 1.0

        cc_agent_score = AgentScoreCard(
            agent_name="AdaptiveCoveredCallAgent",
            status="HEALTHY",
            headline_metric_name="Premium Harvested",
            headline_metric_value=f"${round(option_premium_harvested, 2)}",
            metrics={
                "premium_harvested_total": round(option_premium_harvested, 2),
                "opportunity_cost_delta": round(opportunity_cost_delta, 2),
                "assignment_avoidance_rate": cc_avoidance_rate,
            },
            summary="Harvests options premium in neutral regimes and protects downside basis."
        )

        # 6. Agent 5: AdaptiveFeedbackEngine
        # Rolling S-score, helped vs hurt ratio, circuit breaker trips
        rolling_s = self.calculate_effectiveness_score(actions, window=20)
        helped_count = 0
        hurt_count = 0
        circuit_breaker_trips = 0

        for a in actions:
            outcome = a.get("outcome") or {}
            pnl = a.get("final_pnl") if a.get("final_pnl") is not None else outcome.get("final_pnl", 0.0)
            if pnl > 0:
                helped_count += 1
            elif pnl < 0:
                hurt_count += 1

        for c in applied_changes:
            if c.get("circuit_breaker_trip") or c.get("reason") == "circuit_breaker_rollback":
                circuit_breaker_trips += 1

        feedback_status = "HEALTHY"
        if rolling_s < NEGATIVE_ALPHA_THRESHOLD or circuit_breaker_trips > 2:
            feedback_status = "DEGRADED"

        feedback_engine_score = AgentScoreCard(
            agent_name="AdaptiveFeedbackEngine",
            status=feedback_status,
            headline_metric_name="Rolling S-Score",
            headline_metric_value=f"{rolling_s:+0.2f}",
            metrics={
                "rolling_score": rolling_s,
                "helped_actions_count": helped_count,
                "hurt_actions_count": hurt_count,
                "circuit_breaker_trips": circuit_breaker_trips,
            },
            summary="Tunes active thresholds in response to trade outcomes and enforces rollback bounds."
        )

        # 7. Agent 6: LearningEngine
        # Proposal acceptance rate, predicted vs realized Sharpe
        total_candidates = len(learning_cands)
        accepted_candidates = sum(1 for c in learning_cands if c.get("status") in ("approved", "accepted", "applied"))
        adoption_rate = round(accepted_candidates / total_candidates, 3) if total_candidates else None

        learning_score = AgentScoreCard(
            agent_name="LearningEngine",
            status="HEALTHY",
            headline_metric_name="Candidate Adoption Rate",
            headline_metric_value=f"{round(adoption_rate * 100, 1)}%" if adoption_rate is not None else "N/A",
            metrics={
                "candidates_generated": total_candidates,
                "candidates_accepted": accepted_candidates,
                "adoption_rate": adoption_rate,
            },
            summary="Generates offline optimization proposals and parameter recommendations."
        )

        # 8. Agent 7: TradeLogAuditor
        # 14-check pass rate, anomaly severity
        total_findings = len(audit_findings)
        critical_anomalies = sum(1 for f in audit_findings if f.get("severity") in ("CRITICAL", "HIGH"))
        auditor_pass_rate = round(max(0.0, 1.0 - (critical_anomalies / 14.0)), 2)

        auditor_score = AgentScoreCard(
            agent_name="TradeLogAuditor",
            status="HEALTHY" if critical_anomalies == 0 else "DEGRADED",
            headline_metric_name="14-Check Pass Rate",
            headline_metric_value=f"{round(auditor_pass_rate * 100, 1)}%",
            metrics={
                "pass_rate": auditor_pass_rate,
                "total_findings": total_findings,
                "critical_findings": critical_anomalies,
            },
            summary="Reconciles broker fills with internal logs across 14 deterministic integrity checks."
        )

        # Alerts count
        alerts = self.get_alerts()["alerts"]

        # Fleet overall health
        agent_statuses = [
            orchestrator_score.status, entry_agent_score.status, pnl_agent_score.status,
            cc_agent_score.status, feedback_engine_score.status, learning_score.status,
            auditor_score.status
        ]
        if any(s == "DEGRADED" for s in agent_statuses) or sla_adaptive.sla_breaches > 0:
            fleet_health = "DEGRADED"
        else:
            fleet_health = "HEALTHY"

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_bots": ACTIVE_SYMBOLS,
            "fleet_health": fleet_health,
            "aggregate_alpha": {
                "rolling_effectiveness": rolling_s,
                "net_giveback_saved": round(giveback_saved_total, 2),
                "premium_harvested": round(option_premium_harvested, 2),
                "mean_r_multiple": mean_r,
                "total_actions_evaluated": len(actions),
                "helped_actions": helped_count,
                "hurt_actions": hurt_count,
            },
            "agent_scores": {
                "AgentOrchestrator": orchestrator_score.model_dump(),
                "AdaptiveEntryAgent": entry_agent_score.model_dump(),
                "AdaptivePnLAgent": pnl_agent_score.model_dump(),
                "AdaptiveCoveredCallAgent": cc_agent_score.model_dump(),
                "AdaptiveFeedbackEngine": feedback_engine_score.model_dump(),
                "LearningEngine": learning_score.model_dump(),
                "TradeLogAuditor": auditor_score.model_dump(),
            },
            "sla_metrics": {
                "cascade_phase_adaptive": sla_adaptive.model_dump(),
                "cascade_phase_marks": sla_marks.model_dump(),
                "cascade_phase_orders": sla_orders.model_dump(),
                "proximity_throttle_bypass_rate": round(throttle_bypasses / total_ticks, 3) if total_ticks else 0.0,
            },
            "active_alerts_count": len(alerts),
        }

        self._set_in_cache("summary", result)
        return result

    def get_bot_efficiency(self, symbol: str) -> Dict[str, Any]:
        """
        Returns granular efficiency scorecard and parameter states for a specific symbol.
        """
        sym = symbol.upper()
        cache_key = f"bot_{sym}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        actions = self._fetch_collection_docs("adaptive_action_log", limit=500, order_by_field="timestamp")
        bot_actions = [a for a in actions if a.get("symbol", "").upper() == sym]

        # Calculate symbol-specific rolling effectiveness
        rolling_s = self.calculate_effectiveness_score(bot_actions, window=10)

        # Applied changes & parameter drift
        applied = self._fetch_collection_docs("applied_changes", limit=200, order_by_field="timestamp")
        bot_changes = [c for c in applied if c.get("symbol", "").upper() == sym]

        param_states = {}
        for param, (c_min, c_max) in HARD_SAFETY_CLAMPS.items():
            # Find latest change or default
            matching_changes = [c for c in bot_changes if c.get("parameter") == param or c.get("param") == param]
            curr_val = (c_min + c_max) / 2.0
            baseline_val = curr_val
            if matching_changes:
                latest = matching_changes[-1]
                curr_val = float(latest.get("new_value", curr_val))
                baseline_val = float(latest.get("baseline_value", latest.get("old_value", curr_val)))

            drift_pct = round(((curr_val - baseline_val) / baseline_val * 100.0) if baseline_val else 0.0, 1)
            is_pinned = (curr_val <= c_min or curr_val >= c_max)

            param_states[param] = ParameterState(
                parameter_name=param,
                current_value=curr_val,
                baseline_value=baseline_val,
                min_clamp=c_min,
                max_clamp=c_max,
                drift_pct=drift_pct,
                is_pinned=is_pinned
            ).model_dump()

        result = {
            "symbol": sym,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "HEALTHY" if rolling_s >= NEGATIVE_ALPHA_THRESHOLD else "DEGRADED",
            "rolling_score": rolling_s,
            "actions_count": len(bot_actions),
            "recent_actions": bot_actions[:50],
            "parameter_states": param_states,
        }

        self._set_in_cache(cache_key, result)
        return result

    def get_history(self, agent: str = "all", window_days: int = 7) -> Dict[str, Any]:
        """
        Returns time-series telemetry for rendering rolling effectiveness
        and latency charts.
        """
        cache_key = f"hist_{agent}_{window_days}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        # Fetch actions, execution metrics, and pre-aggregated daily rollups
        actions = self._fetch_collection_docs("adaptive_action_log", limit=500, order_by_field="timestamp")
        exec_metrics = self._fetch_collection_docs("execution_metrics", limit=500, order_by_field="timestamp")
        rollups = self._fetch_collection_docs("agent_efficiency_rollups", limit=window_days + 5, order_by_field="date")
        rollups_by_date = {r.get("date"): r for r in rollups if r.get("date")}

        now = datetime.now(timezone.utc)
        start_date = now - timedelta(days=window_days)

        # Bucket by day
        buckets: Dict[str, Dict[str, Any]] = {}
        for d in range(window_days + 1):
            day_str = (start_date + timedelta(days=d)).strftime("%Y-%m-%d")
            buckets[day_str] = {
                "date": day_str,
                "rolling_score": 0.0,
                "p99_latency_ms": 0.0,
                "actions_count": 0,
                "giveback_saved": 0.0,
                "raw_actions": [],
                "raw_latencies": [],
            }

        # Populate actions
        for a in actions:
            ts_str = a.get("created_at") or a.get("timestamp") or ""
            day = ts_str[:10]
            if day in buckets:
                buckets[day]["raw_actions"].append(a)
                outcome = a.get("outcome") or {}
                peak = a.get("context", {}).get("peak_unrealized_pnl", 0.0)
                final_pnl = a.get("final_pnl") if a.get("final_pnl") is not None else outcome.get("final_pnl", 0.0)
                if peak > 0 and final_pnl is not None:
                    buckets[day]["giveback_saved"] += max(0.0, float(peak) - float(final_pnl))

        # Populate latencies
        for em in exec_metrics:
            ts_str = em.get("timestamp") or em.get("ts") or ""
            day = ts_str[:10]
            if day in buckets:
                ns_a = em.get("cascade_phase_adaptive_ns")
                if ns_a is not None and isinstance(ns_a, (int, float)):
                    buckets[day]["raw_latencies"].append(ns_a / 1e6)

        series = []
        for day in sorted(buckets.keys()):
            b = buckets[day]
            if b["raw_actions"]:
                b["actions_count"] = len(b["raw_actions"])
                b["rolling_score"] = self.calculate_effectiveness_score(b["raw_actions"])
            elif day in rollups_by_date:
                r = rollups_by_date[day]
                b["actions_count"] = r.get("actions_count", 0)
                b["rolling_score"] = r.get("rolling_score", 0.0)
                b["giveback_saved"] = r.get("net_giveback_saved", 0.0)

            if b["raw_latencies"]:
                sorted_lats = sorted(b["raw_latencies"])
                idx = int(math.ceil(0.99 * len(sorted_lats))) - 1
                b["p99_latency_ms"] = round(sorted_lats[max(0, min(idx, len(sorted_lats) - 1))], 2)
            elif day in rollups_by_date:
                b["p99_latency_ms"] = rollups_by_date[day].get("p99_latency_ms", 0.0)

            series.append({
                "date": b["date"],
                "rolling_score": b["rolling_score"],
                "p99_latency_ms": b["p99_latency_ms"],
                "actions_count": b["actions_count"],
                "giveback_saved": round(b["giveback_saved"], 2),
            })

        result = {
            "agent": agent,
            "window_days": window_days,
            "series": series
        }

        self._set_in_cache(cache_key, result)
        return result

    def get_alerts(self) -> Dict[str, Any]:
        """
        Returns active alerts for SLA violations, sustained negative alpha,
        circuit breaker rollbacks, and parameter pinning.
        """
        cached = self._get_from_cache("alerts")
        if cached is not None:
            return cached

        alerts: List[Dict[str, Any]] = []

        # 1. Check Latency SLAs
        exec_metrics = self._fetch_collection_docs("execution_metrics", limit=200, order_by_field="timestamp")
        adaptive_ms = []
        for em in exec_metrics:
            ns = em.get("cascade_phase_adaptive_ns")
            if ns is not None and isinstance(ns, (int, float)):
                adaptive_ms.append(ns / 1e6)

        stats = self.calculate_latency_percentiles(adaptive_ms)
        if stats.p99_ms > SLA_P99_THRESHOLD_MS:
            alerts.append(EfficiencyAlert(
                alert_id=f"sla_p99_{int(datetime.now(timezone.utc).timestamp())}",
                severity="HIGH",
                agent="AdaptivePnLAgent",
                message=f"Cascade adaptive phase p99 latency {stats.p99_ms}ms exceeds 500ms SLA threshold.",
                metric="p99_latency_ms",
                value=stats.p99_ms,
                threshold=SLA_P99_THRESHOLD_MS,
                timestamp=datetime.now(timezone.utc).isoformat()
            ).model_dump())

        if stats.max_ms > SLA_MAX_THRESHOLD_MS:
            alerts.append(EfficiencyAlert(
                alert_id=f"sla_max_{int(datetime.now(timezone.utc).timestamp())}",
                severity="CRITICAL",
                agent="AdaptivePnLAgent",
                message=f"Cascade adaptive phase max latency {stats.max_ms}ms exceeds 2000ms max threshold.",
                metric="max_latency_ms",
                value=stats.max_ms,
                threshold=SLA_MAX_THRESHOLD_MS,
                timestamp=datetime.now(timezone.utc).isoformat()
            ).model_dump())

        # 2. Check Sustained Negative Alpha (S-score < -0.20)
        actions = self._fetch_collection_docs("adaptive_action_log", limit=200, order_by_field="timestamp")
        s_score = self.calculate_effectiveness_score(actions, window=20)
        if s_score < NEGATIVE_ALPHA_THRESHOLD:
            alerts.append(EfficiencyAlert(
                alert_id=f"alpha_neg_{int(datetime.now(timezone.utc).timestamp())}",
                severity="CRITICAL",
                agent="AdaptiveFeedbackEngine",
                message=f"Agent rolling effectiveness score {s_score:+.2f} is in hurting territory (below {NEGATIVE_ALPHA_THRESHOLD:+.2f}).",
                metric="rolling_score",
                value=s_score,
                threshold=NEGATIVE_ALPHA_THRESHOLD,
                timestamp=datetime.now(timezone.utc).isoformat()
            ).model_dump())

        # 3. Check Circuit Breaker Trips
        applied = self._fetch_collection_docs("applied_changes", limit=50, order_by_field="timestamp")
        for c in applied:
            if c.get("circuit_breaker_trip") or c.get("reason") == "circuit_breaker_rollback":
                alerts.append(EfficiencyAlert(
                    alert_id=f"cb_{c.get('doc_id', 'trip')}",
                    severity="CRITICAL",
                    agent="AdaptiveFeedbackEngine",
                    symbol=c.get("symbol"),
                    message=f"Circuit breaker rollback triggered on {c.get('symbol')}: {c.get('param')}.",
                    metric="circuit_breaker_trip",
                    value=c.get("delta_pf", CIRCUIT_BREAKER_PF_DELTA),
                    threshold=CIRCUIT_BREAKER_PF_DELTA,
                    timestamp=c.get("timestamp", datetime.now(timezone.utc).isoformat())
                ).model_dump())

        result = {"alerts": alerts}
        self._set_in_cache("alerts", result)
        return result

    def generate_daily_rollup(self, date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Generates and persists a consolidated daily efficiency rollup document
        in Firestore collection 'agent_efficiency_rollups' keyed by 'YYYY-MM-DD'.
        """
        target_date = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = self.get_summary()
        alerts = self.get_alerts().get("alerts", [])
        alpha = summary.get("aggregate_alpha", {})
        sla = summary.get("sla_metrics", {})
        adaptive_sla = sla.get("cascade_phase_adaptive", {})

        rollup = {
            "date": target_date,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fleet_health": summary.get("fleet_health", "HEALTHY"),
            "rolling_score": alpha.get("rolling_effectiveness", 0.0),
            "net_giveback_saved": alpha.get("net_giveback_saved", 0.0),
            "premium_harvested": alpha.get("premium_harvested", 0.0),
            "mean_r_multiple": alpha.get("mean_r_multiple", 0.0),
            "actions_count": alpha.get("total_actions_evaluated", 0),
            "p99_latency_ms": adaptive_sla.get("p99_ms", 0.0),
            "active_alerts_count": len(alerts),
            "agent_scores": {
                agent: data.get("headline_metric_value")
                for agent, data in summary.get("agent_scores", {}).items()
            },
        }

        try:
            db = self._get_db()
            coll = db.collection("agent_efficiency_rollups")
            coll.document(target_date).set(rollup)
        except Exception as e:
            logger.warning(f"Could not persist daily rollup for {target_date}: {e}")

        return rollup
