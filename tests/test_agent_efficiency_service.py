"""
Hermetic unit tests for AgentEfficiencyService.
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from services.agent_efficiency_service import (
    AgentEfficiencyService,
    LatencyStats,
    HARD_SAFETY_CLAMPS,
    SLA_P99_THRESHOLD_MS,
    SLA_MAX_THRESHOLD_MS,
    NEGATIVE_ALPHA_THRESHOLD,
)


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = dict(data)

    def to_dict(self):
        return dict(self._data)


class _FakeDocRef:
    def __init__(self, doc_id, storage):
        self.id = doc_id
        self._storage = storage

    def set(self, data):
        self._storage[self.id] = data


class _FakeStream:
    def __init__(self, docs):
        self._docs = list(docs)

    def stream(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self, docs, storage=None, fail_order_by=False):
        self._docs = list(docs)
        self._storage = storage if storage is not None else {}
        self._fail_order_by = fail_order_by

    def limit(self, n):
        return _FakeStream(self._docs[:n])

    def stream(self):
        return iter(self._docs)

    def document(self, doc_id):
        return _FakeDocRef(doc_id, self._storage)

    def order_by(self, field, direction=None):
        if self._fail_order_by:
            raise RuntimeError(f"Missing index on {field}")
        rev = "DESC" in str(direction).upper()
        sorted_docs = sorted(self._docs, key=lambda d: str(d.to_dict().get(field) or ""), reverse=rev)
        return _FakeCollection(sorted_docs, storage=self._storage, fail_order_by=self._fail_order_by)


class _FakeDb:
    def __init__(self, collections=None, fail_order_by=False):
        self._collections = collections or {}
        self._fail_order_by = fail_order_by
        self.storage = {}

    def collection(self, name):
        docs = self._collections.get(name, [])
        coll_storage = self.storage.setdefault(name, {})
        return _FakeCollection(
            [_FakeDoc(d.get("doc_id", f"{name}_{i}"), d) for i, d in enumerate(docs)],
            storage=coll_storage,
            fail_order_by=self._fail_order_by
        )


class TestAgentEfficiencyService(unittest.TestCase):
    def setUp(self):
        self.mock_db = _FakeDb()
        self.service = AgentEfficiencyService(db=self.mock_db, cache_ttl=60.0)

    def test_calculate_latency_percentiles_empty(self):
        stats = AgentEfficiencyService.calculate_latency_percentiles([])
        self.assertEqual(stats.count, 0)
        self.assertEqual(stats.p50_ms, 0.0)
        self.assertEqual(stats.p99_ms, 0.0)
        self.assertEqual(stats.max_ms, 0.0)
        self.assertEqual(stats.sla_breaches, 0)

    def test_calculate_latency_percentiles_populated(self):
        # 100 values from 1ms to 1000ms
        durations = [i * 10.0 for i in range(1, 101)]
        stats = AgentEfficiencyService.calculate_latency_percentiles(durations)
        self.assertEqual(stats.count, 100)
        self.assertEqual(stats.p50_ms, 500.0)
        self.assertEqual(stats.p95_ms, 950.0)
        self.assertEqual(stats.p99_ms, 990.0)
        self.assertEqual(stats.max_ms, 1000.0)
        # Breaches above 500ms: 510..1000 = 50 values
        self.assertEqual(stats.sla_breaches, 50)

    def test_calculate_effectiveness_score(self):
        # Empty actions
        self.assertEqual(AgentEfficiencyService.calculate_effectiveness_score([]), 0.0)

        # All positive actions
        actions_pos = [
            {"resolved": True, "final_pnl": 150.0, "realized_r": 1.5},
            {"resolved": True, "final_pnl": 50.0, "realized_r": 0.5},
        ]
        self.assertEqual(AgentEfficiencyService.calculate_effectiveness_score(actions_pos), 1.0)

        # All negative actions
        actions_neg = [
            {"resolved": True, "final_pnl": -150.0, "realized_r": -1.0},
            {"resolved": True, "final_pnl": -50.0, "realized_r": -0.5},
        ]
        self.assertEqual(AgentEfficiencyService.calculate_effectiveness_score(actions_neg), -1.0)

        # Mixed actions with outcome sub-dict
        actions_mixed = [
            {"outcome": {"resolved": True, "final_pnl": 100.0}},
            {"outcome": {"resolved": True, "final_pnl": -100.0}},
            {"outcome": {"resolved": False, "final_pnl": 0.0}},  # unresolved ignored
        ]
        self.assertEqual(AgentEfficiencyService.calculate_effectiveness_score(actions_mixed), 0.0)

        # Window limiting
        actions_rolling = [{"resolved": True, "final_pnl": -10.0}] * 20 + [{"resolved": True, "final_pnl": 10.0}] * 5
        # With window=5, should only take last 5 positive actions
        self.assertEqual(AgentEfficiencyService.calculate_effectiveness_score(actions_rolling, window=5), 1.0)

    def test_get_summary_empty_collections(self):
        summary = self.service.get_summary()
        self.assertIn("timestamp", summary)
        self.assertEqual(summary["fleet_health"], "HEALTHY")
        self.assertEqual(len(summary["agent_scores"]), 7)
        self.assertIn("AgentOrchestrator", summary["agent_scores"])
        self.assertIn("AdaptivePnLAgent", summary["agent_scores"])
        self.assertIn("AdaptiveFeedbackEngine", summary["agent_scores"])
        self.assertIn("TradeLogAuditor", summary["agent_scores"])

    def test_get_summary_with_mock_data(self):
        db_data = {
            "adaptive_action_log": [
                {
                    "symbol": "NVDA",
                    "action_type": "break_even_lock",
                    "resolved": True,
                    "final_pnl": 250.0,
                    "realized_r": 1.2,
                    "context": {"peak_unrealized_pnl": 400.0},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "symbol": "IWM",
                    "action_type": "entry_filter",
                    "resolved": True,
                    "final_pnl": 100.0,
                    "context": {"block_reason": "none"},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "symbol": "IWM",
                    "action_type": "entry_blocked_mtf",
                    "context": {"block_reason": "entry_blocked_mtf"},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "symbol": "QQQ",
                    "action_type": "covered_call_harvest",
                    "context": {
                        "premium_harvested": 180.0,
                        "equity_upside_capped": 50.0,
                        "assigned": False,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            ],
            "applied_changes": [
                {
                    "symbol": "NVDA",
                    "parameter": "break_even_lock_r_multiple",
                    "old_value": 1.0,
                    "new_value": 1.05,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "execution_metrics": [
                {
                    "cascade_phase_adaptive_ns": 450_000_000,  # 450ms
                    "cascade_phase_marks_ns": 20_000_000,      # 20ms
                    "cascade_phase_orders_ns": 15_000_000,     # 15ms
                    "proximity_throttled": True,
                },
                {
                    "cascade_phase_adaptive_ns": 200_000_000,  # 200ms
                    "cascade_phase_marks_ns": 25_000_000,
                    "cascade_phase_orders_ns": 10_000_000,
                    "proximity_throttled": False,
                },
            ],
            "learning_candidates": [
                {"candidate_id": "c1", "status": "approved"},
                {"candidate_id": "c2", "status": "rejected"},
            ],
            "audit_findings": [
                {"finding_id": "f1", "severity": "LOW"},
            ],
            "bot_overview": [
                {"symbol": "NVDA", "regime_status": "BULL", "strategy_intent": "TREND_MOMENTUM_GROWTH"},
                {"symbol": "IWM", "regime_status": "NEUTRAL", "strategy_intent": "NEUTRAL_RANGE_HARVEST"},
            ],
        }
        service = AgentEfficiencyService(db=_FakeDb(db_data))
        summary = service.get_summary()

        self.assertEqual(summary["fleet_health"], "HEALTHY")
        self.assertAlmostEqual(summary["aggregate_alpha"]["net_giveback_saved"], 150.0)
        self.assertAlmostEqual(summary["aggregate_alpha"]["premium_harvested"], 180.0)

        # Check AgentOrchestrator
        orch = summary["agent_scores"]["AgentOrchestrator"]
        self.assertEqual(orch["status"], "HEALTHY")
        self.assertEqual(orch["metrics"]["intent_match_rate"], 1.0)

        # Check AdaptiveEntryAgent
        entry = summary["agent_scores"]["AdaptiveEntryAgent"]
        self.assertEqual(entry["metrics"]["total_signals_evaluated"], 2)
        self.assertEqual(entry["metrics"]["selectivity_ratio"], 0.5)

        # Check AdaptivePnLAgent
        pnl = summary["agent_scores"]["AdaptivePnLAgent"]
        self.assertEqual(pnl["metrics"]["realized_r_mean"], 1.2)
        self.assertEqual(pnl["metrics"]["breakeven_conversion_rate"], 1.0)

        # Check AdaptiveCoveredCallAgent
        cc = summary["agent_scores"]["AdaptiveCoveredCallAgent"]
        self.assertEqual(cc["metrics"]["premium_harvested_total"], 180.0)
        self.assertEqual(cc["metrics"]["opportunity_cost_delta"], 130.0)

        # Check LearningEngine
        learning = summary["agent_scores"]["LearningEngine"]
        self.assertEqual(learning["metrics"]["candidates_generated"], 2)
        self.assertEqual(learning["metrics"]["adoption_rate"], 0.5)

        # Check TradeLogAuditor
        auditor = summary["agent_scores"]["TradeLogAuditor"]
        self.assertEqual(auditor["status"], "HEALTHY")
        self.assertEqual(auditor["metrics"]["critical_findings"], 0)

        # Check caching: subsequent call returns identical object
        cached_summary = service.get_summary()
        self.assertEqual(summary["timestamp"], cached_summary["timestamp"])

    def test_get_bot_efficiency(self):
        db_data = {
            "adaptive_action_log": [
                {
                    "symbol": "NVDA",
                    "action_type": "break_even_lock",
                    "resolved": True,
                    "final_pnl": 200.0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "applied_changes": [
                {
                    "symbol": "NVDA",
                    "parameter": "break_even_lock_r_multiple",
                    "baseline_value": 1.0,
                    "new_value": 2.0,  # Max clamp pinned
                }
            ]
        }
        service = AgentEfficiencyService(db=_FakeDb(db_data))
        res = service.get_bot_efficiency("NVDA")

        self.assertEqual(res["symbol"], "NVDA")
        self.assertEqual(res["status"], "HEALTHY")
        self.assertEqual(res["rolling_score"], 1.0)
        self.assertEqual(res["actions_count"], 1)

        # Check pinned clamp
        be_param = res["parameter_states"]["break_even_lock_r_multiple"]
        self.assertTrue(be_param["is_pinned"])
        self.assertEqual(be_param["current_value"], 2.0)
        self.assertEqual(be_param["drift_pct"], 100.0)

    def test_get_history(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db_data = {
            "adaptive_action_log": [
                {
                    "symbol": "NVDA",
                    "resolved": True,
                    "final_pnl": 100.0,
                    "created_at": f"{today}T12:00:00Z",
                    "context": {"peak_unrealized_pnl": 150.0},
                }
            ],
            "execution_metrics": [
                {
                    "timestamp": f"{today}T12:00:00Z",
                    "cascade_phase_adaptive_ns": 300_000_000,
                }
            ]
        }
        service = AgentEfficiencyService(db=_FakeDb(db_data))
        hist = service.get_history(agent="AdaptivePnLAgent", window_days=7)

        self.assertEqual(hist["agent"], "AdaptivePnLAgent")
        self.assertEqual(hist["window_days"], 7)
        self.assertTrue(len(hist["series"]) >= 7)

        today_point = [p for p in hist["series"] if p["date"] == today][0]
        self.assertEqual(today_point["actions_count"], 1)
        self.assertEqual(today_point["rolling_score"], 1.0)
        self.assertEqual(today_point["p99_latency_ms"], 300.0)
        self.assertEqual(today_point["giveback_saved"], 50.0)

    def test_get_alerts_triggers(self):
        db_data = {
            "execution_metrics": [
                {"cascade_phase_adaptive_ns": 600_000_000},    # 600ms > 500ms SLA
                {"cascade_phase_adaptive_ns": 2_500_000_000},  # 2500ms > 2000ms MAX SLA
            ],
            "adaptive_action_log": [
                {"resolved": True, "final_pnl": -100.0} for _ in range(10)
            ],
            "applied_changes": [
                {
                    "doc_id": "trip_001",
                    "symbol": "NVDA",
                    "circuit_breaker_trip": True,
                    "param": "break_even_lock_r_multiple",
                    "delta_pf": -0.22,
                }
            ]
        }
        service = AgentEfficiencyService(db=_FakeDb(db_data))
        alerts_res = service.get_alerts()
        alerts = alerts_res["alerts"]

        self.assertTrue(any(a["metric"] == "p99_latency_ms" for a in alerts))
        self.assertTrue(any(a["metric"] == "max_latency_ms" for a in alerts))
        self.assertTrue(any(a["metric"] == "rolling_score" and a["value"] == -1.0 for a in alerts))
        self.assertTrue(any(a["metric"] == "circuit_breaker_trip" for a in alerts))

    def test_cache_clear_and_lazy_db(self):
        service = AgentEfficiencyService(db=None)
        with patch("google.cloud.firestore.Client") as mock_fs:
            mock_fs.return_value = self.mock_db
            db = service._get_db()
            self.assertEqual(db, self.mock_db)

        # Test error initializing db
        service_err = AgentEfficiencyService(db=None)
        with patch("google.cloud.firestore.Client", side_effect=Exception("client init error")):
            with self.assertRaises(Exception):
                service_err._get_db()

        # Test cache clear and expiration
        service._set_in_cache("test_key", "test_val")
        self.assertEqual(service._get_from_cache("test_key"), "test_val")
        service.clear_cache()
        self.assertIsNone(service._get_from_cache("test_key"))

        # Test TTL expiration
        service.cache_ttl = 0.001
        service._set_in_cache("exp_key", "exp_val")
        import time
        time.sleep(0.01)
        self.assertIsNone(service._get_from_cache("exp_key"))

    def test_edge_cases_and_coverage(self):
        # 1. Fallback when coll.limit() raises
        class _FailingLimitColl:
            def limit(self, n):
                raise RuntimeError("no limit supported")
            def stream(self):
                return iter([_FakeDoc("d1", {"val": 1})])

        class _DbWithFailingLimit:
            def collection(self, name):
                return _FailingLimitColl()

        svc = AgentEfficiencyService(db=_DbWithFailingLimit())
        docs = svc._fetch_collection_docs("any")
        self.assertEqual(len(docs), 1)

        # 2. Entire collection fetching raises exception
        class _CrashingDb:
            def collection(self, name):
                raise RuntimeError("db down")

        svc_crash = AgentEfficiencyService(db=_CrashingDb())
        docs_crash = svc_crash._fetch_collection_docs("any")
        self.assertEqual(docs_crash, [])

        # 3. Admitted loss and degraded status
        db_degraded = {
            "adaptive_action_log": [
                {
                    "symbol": "NVDA",
                    "action_type": "entry_admitted",
                    "outcome": {"resolved": True, "final_pnl": -50.0},
                    "final_pnl": -50.0,
                    "created_at": "2026-11-20T10:00:00Z"
                }
            ],
            "applied_changes": [
                {"circuit_breaker_trip": True, "symbol": "NVDA", "param": "p1"},
                {"circuit_breaker_trip": True, "symbol": "NVDA", "param": "p2"},
                {"circuit_breaker_trip": True, "symbol": "NVDA", "param": "p3"},
            ],
            "execution_metrics": [
                {"cascade_phase_adaptive_ns": 600_000_000}
            ],
            "bot_overview": [
                {"symbol": "NVDA", "regime_status": "BULL", "strategy_intent": "DEFENSIVE"}  # mismatch -> degraded
            ]
        }
        svc_deg = AgentEfficiencyService(db=_FakeDb(db_degraded))
        sum_deg = svc_deg.get_summary()
        self.assertEqual(sum_deg["fleet_health"], "DEGRADED")
        self.assertEqual(sum_deg["agent_scores"]["AdaptiveFeedbackEngine"]["status"], "DEGRADED")

        # 4. Cache hit branches for get_bot_efficiency, get_history, get_alerts
        bot_res1 = svc_deg.get_bot_efficiency("NVDA")
        bot_res2 = svc_deg.get_bot_efficiency("NVDA")
        self.assertEqual(bot_res1["timestamp"], bot_res2["timestamp"])

        hist_res1 = svc_deg.get_history("AdaptivePnLAgent", 3)
        hist_res2 = svc_deg.get_history("AdaptivePnLAgent", 3)
        self.assertEqual(hist_res1["agent"], hist_res2["agent"])

        alerts1 = svc_deg.get_alerts()
        alerts2 = svc_deg.get_alerts()
        self.assertEqual(len(alerts1["alerts"]), len(alerts2["alerts"]))

        # 5. Baseline value = 0 in param drift
        db_zero_base = {
            "applied_changes": [
                {"symbol": "NVDA", "parameter": "break_even_lock_r_multiple", "baseline_value": 0.0, "new_value": 1.0}
            ]
        }
        svc_zero = AgentEfficiencyService(db=_FakeDb(db_zero_base))
        bot_zero = svc_zero.get_bot_efficiency("NVDA")
        self.assertEqual(bot_zero["parameter_states"]["break_even_lock_r_multiple"]["drift_pct"], 0.0)

    def test_cache_lru_eviction(self):
        """Verifies LRU capacity clamping and expired entry eviction."""
        svc = AgentEfficiencyService(db=_FakeDb(), cache_ttl=1.0, max_cache_entries=16)
        # Verify min clamp
        self.assertEqual(svc.max_cache_entries, 16)

        # Manually set max_cache_entries to 3 to test eviction mechanics
        svc.max_cache_entries = 3

        svc._set_in_cache("k1", "v1")
        svc._set_in_cache("k2", "v2")
        svc._set_in_cache("k3", "v3")
        self.assertEqual(len(svc._cache), 3)

        # Adding k4 should evict oldest (k1)
        svc._set_in_cache("k4", "v4")
        self.assertEqual(len(svc._cache), 3)
        self.assertIsNone(svc._get_from_cache("k1"))
        self.assertEqual(svc._get_from_cache("k4"), "v4")

        # Manually expire k2 and verify expired eviction
        svc._cache["k2"] = (datetime.now(timezone.utc) - timedelta(seconds=10), "v2")
        svc._set_in_cache("k5", "v5")
        self.assertEqual(len(svc._cache), 3)
        self.assertNotIn("k2", svc._cache)
        self.assertEqual(svc._get_from_cache("k5"), "v5")

    def test_generate_daily_rollup_and_persist(self):
        """Verifies rollup generation, calculation, and persistence."""
        fake_db = _FakeDb()
        svc = AgentEfficiencyService(db=fake_db)
        rollup = svc.generate_daily_rollup("2026-11-21")

        self.assertEqual(rollup["date"], "2026-11-21")
        self.assertEqual(rollup["fleet_health"], "HEALTHY")
        self.assertIn("rolling_score", rollup)
        self.assertIn("p99_latency_ms", rollup)
        self.assertIn("agent_scores", rollup)

        # Check stored in fake db
        stored = fake_db.storage.get("agent_efficiency_rollups", {}).get("2026-11-21")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["date"], "2026-11-21")

        # Test failure resilience when set() throws
        failing_db = MagicMock()
        failing_db.collection.return_value.document.return_value.set.side_effect = RuntimeError("Storage write failure")
        svc_fail = AgentEfficiencyService(db=failing_db)
        # Should not raise exception
        rollup_fail = svc_fail.generate_daily_rollup("2026-11-21")
        self.assertEqual(rollup_fail["date"], "2026-11-21")

    def test_history_with_preaggregated_rollups(self):
        """Verifies get_history utilizes pre-aggregated daily rollup documents."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        db_data = {
            "agent_efficiency_rollups": [
                {
                    "date": yesterday,
                    "rolling_score": 0.75,
                    "net_giveback_saved": 250.0,
                    "p99_latency_ms": 115.5,
                    "actions_count": 8,
                }
            ],
            "adaptive_action_log": [],  # no raw actions for yesterday
            "execution_metrics": [],
        }

        fake_db = _FakeDb(db_data)
        svc = AgentEfficiencyService(db=fake_db)
        hist = svc.get_history(agent="all", window_days=3)

        day_entry = next((s for s in hist["series"] if s["date"] == yesterday), None)
        self.assertIsNotNone(day_entry)
        self.assertEqual(day_entry["rolling_score"], 0.75)
        self.assertEqual(day_entry["giveback_saved"], 250.0)
        self.assertEqual(day_entry["p99_latency_ms"], 115.5)
        self.assertEqual(day_entry["actions_count"], 8)

    def test_fetch_collection_docs_native_and_fallback_sorting(self):
        """Verifies native order_by and fallback sorting mechanics."""
        raw_docs = [
            {"timestamp": "2026-11-20T10:00:00Z", "val": 10},
            {"timestamp": "2026-11-20T12:00:00Z", "val": 20},
            {"timestamp": "2026-11-20T08:00:00Z", "val": 5},
        ]

        # 1. Native order_by path
        db_native = _FakeDb({"test_coll": raw_docs}, fail_order_by=False)
        svc_native = AgentEfficiencyService(db=db_native)
        res_native = svc_native._fetch_collection_docs("test_coll", limit=2, order_by_field="timestamp")
        self.assertEqual(len(res_native), 2)
        self.assertEqual(res_native[0]["val"], 20)
        self.assertEqual(res_native[1]["val"], 10)

        # 2. Fallback in-memory sort path when order_by raises exception
        db_fallback = _FakeDb({"test_coll": raw_docs}, fail_order_by=True)
        svc_fallback = AgentEfficiencyService(db=db_fallback)
        res_fallback = svc_fallback._fetch_collection_docs("test_coll", limit=2, order_by_field="timestamp")
        self.assertEqual(len(res_fallback), 2)
        self.assertEqual(res_fallback[0]["val"], 20)
        self.assertEqual(res_fallback[1]["val"], 10)


if __name__ == "__main__":
    unittest.main()
