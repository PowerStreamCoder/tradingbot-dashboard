#!/usr/bin/env python3
"""
Tests for the governance review API endpoints and page.

Covers: /api/exit-governance, /api/applied-changes,
/api/adaptive-actions, /api/exit-telemetry/snapshots, and the
/governance-review page route, plus the auth gate on the new APIs.
"""

import datetime
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

import main

SESSION_ID = "govtest-abcdef"
FUTURE = datetime.datetime.now() + datetime.timedelta(hours=1)


class _FakeDb:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        store = self

        class _Doc:
            def __init__(self, doc_id):
                self.id = doc_id
                self.exists = True

            def to_dict(self):
                return dict(store.collections[name][self.id])

        class _Query:
            def __init__(self, keys, limit=None):
                self._keys = keys
                self._limit = limit

            def order_by(self, field, direction=None):
                rev = direction == "DESCENDING" or direction == "DESC"
                self._keys.sort(
                    key=lambda k: store.collections[name][k].get(field, ""),
                    reverse=rev,
                )
                return self

            def limit(self, n):
                self._limit = n
                return self

            def stream(self):
                keys = self._keys
                if self._limit is not None:
                    keys = keys[: self._limit]
                return iter([_Doc(k) for k in keys])

        class _Collection:
            def stream(self):
                return _Query(list(store.collections.get(name, {}).keys())).stream()

            def order_by(self, field, direction=None):
                return _Query(list(store.collections.get(name, {}).keys())).order_by(
                    field, direction=direction
                )

        return _Collection()


class TestGovernanceApi(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDb()
        self.db.collections["exit_governance"] = {
            "NVDA": {"symbol": "NVDA", "payload_version": 2, "updated_at": "2026-09-21T22:00:00Z"},
            "AMD": {"symbol": "AMD", "payload_version": 1, "updated_at": "2026-09-21T21:00:00Z"},
        }
        self.db.collections["applied_changes"] = {
            "rec1": {"engine": "adaptive", "field": "adaptive_pnl.scale_out_target_r",
                     "symbol": "NVDA", "new_value": 1.5, "status": "applied",
                     "applied_at": "2026-09-21T22:00:00Z"},
            "rec2": {"engine": "learning", "field": "stop_loss_threshold",
                     "symbol": "AMD", "new_value": 0.95, "status": "applied",
                     "applied_at": "2026-09-21T23:00:00Z"},
        }
        self.db.collections["adaptive_action_log"] = {
            "a1": {"action_type": "scale_out_target_r", "created_at": "2026-09-21T22:00:00Z",
                   "outcome": {"resolved": True, "final_pnl": 12.5}},
            "a2": {"action_type": "scale_out_target_r", "created_at": "2026-09-21T20:00:00Z",
                   "outcome": {"resolved": True, "final_pnl": -4.0}},
            "a3": {"action_type": "profit_target_step", "created_at": "2026-09-21T19:00:00Z",
                   "outcome": {"resolved": False}},
        }
        self.db.collections["exit_telemetry_snapshots"] = {
            "NVDA_20260921_x": {"symbol": "NVDA", "session_id": "x",
                                "archived_at": "2026-09-21T22:00:00Z",
                                "inventory": {"branches": {}}},
            "NVDA_20260920_x": {"symbol": "NVDA", "session_id": "x",
                                "archived_at": "2026-09-20T22:00:00Z",
                                "inventory": {"branches": {}}},
        }

        self.client_patch = patch("main.firestore.Client", return_value=self.db)
        self.client_patch.start()
        self.client = TestClient(main.app)
        main.authenticated_sessions[SESSION_ID] = FUTURE
        self.headers = {"Cookie": f"dashboard_session={SESSION_ID}"}

    def tearDown(self):
        main.authenticated_sessions.pop(SESSION_ID, None)
        self.client_patch.stop()

    def _get(self, path, **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.get(path, headers=headers, **kwargs)

    def test_exit_governance_returns_mirror_payloads(self):
        r = self._get("/api/exit-governance")
        assert r.status_code == 200
        payload = r.json()
        assert {e["symbol"] for e in payload["governance"]} == {"NVDA", "AMD"}
        by_symbol = {e["symbol"]: e for e in payload["governance"]}
        assert by_symbol["AMD"]["payload_version"] == 1
        assert by_symbol["AMD"]["doc_id"] == "AMD"

    def test_applied_changes_sorted_and_filterable(self):
        r = self._get("/api/applied-changes")
        assert r.status_code == 200
        changes = r.json()["changes"]
        assert [c["new_value"] for c in changes] == [0.95, 1.5]

        r2 = self._get("/api/applied-changes", params={"symbol": "AMD"})
        assert len(r2.json()["changes"]) == 1
        assert r2.json()["changes"][0]["symbol"] == "AMD"

    def test_adaptive_actions_stats(self):
        r = self._get("/api/adaptive-actions")
        assert r.status_code == 200
        payload = r.json()
        assert len(payload["actions"]) == 3
        by_type = {s["action_type"]: s for s in payload["stats"]}
        stats = by_type["scale_out_target_r"]
        assert stats["fired"] == 2
        assert stats["resolved"] == 2
        assert stats["win_rate"] == 0.5
        assert by_type["profit_target_step"]["unresolved"] == 1
        assert by_type["profit_target_step"]["win_rate"] is None

    def test_exit_telemetry_snapshots_newest_first_and_limit(self):
        r = self._get("/api/exit-telemetry/snapshots")
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert len(sessions) == 2
        assert sessions[0]["archived_at"] == "2026-09-21T22:00:00Z"

        r2 = self._get("/api/exit-telemetry/snapshots", params={"limit": 1})
        assert len(r2.json()["sessions"]) == 1

    def test_governance_review_page_served(self):
        r = self._get("/governance-review")
        assert r.status_code == 200
        assert "Governance" in r.text

    def test_api_requires_auth(self):
        r = self.client.get("/api/exit-governance")
        assert r.status_code == 401
        r2 = self.client.get("/api/applied-changes")
        assert r2.status_code == 401

    def test_error_path_returns_500(self):
        with patch("main.firestore.Client", side_effect=RuntimeError("boom")):
            r = self._get("/api/exit-governance")
        assert r.status_code == 500


if __name__ == "__main__":
    unittest.main()