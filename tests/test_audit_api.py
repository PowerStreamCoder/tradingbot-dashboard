#!/usr/bin/env python3
"""
Tests for Trade Log Auditor dashboard API and review page.

Covers:
  - GET /audit and GET /audit-review (HTML page)
  - GET /api/audit/runs (list runs, pagination, sorting fallback, error handling)
  - GET /api/audit/runs/{run_id} (get specific run, 404, error handling)
  - GET /api/audit/findings (filtering by status, category, symbol, severity sorting, limit, error handling)
  - Auth gating on /audit, /audit-review, and /api/audit/*
"""

import datetime
import os
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

import main

SESSION_ID = "audittest-sess-12345"
FUTURE = datetime.datetime.now() + datetime.timedelta(hours=2)


class _FakeDoc:
    def __init__(self, doc_id, data, exists=True):
        self.id = doc_id
        self._data = dict(data) if data is not None else {}
        self.exists = exists

    def to_dict(self):
        return dict(self._data)


class _FakeDocRef:
    def __init__(self, doc_id, data, exists=True):
        self.id = doc_id
        self._data = data
        self.exists = exists

    def get(self):
        return _FakeDoc(self.id, self._data, exists=self.exists)


class _FakeQuery:
    def __init__(self, docs):
        self._docs = list(docs)

    def order_by(self, field, direction=None):
        rev = direction in ("DESCENDING", "DESC")
        self._docs.sort(key=lambda d: d.to_dict().get(field, ""), reverse=rev)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def stream(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self, docs_map, fail_order_by=False):
        self._docs_map = docs_map
        self._fail_order_by = fail_order_by

    def document(self, doc_id):
        if doc_id in self._docs_map:
            return _FakeDocRef(doc_id, self._docs_map[doc_id], exists=True)
        return _FakeDocRef(doc_id, {}, exists=False)

    def order_by(self, field, direction=None):
        if self._fail_order_by:
            raise RuntimeError("Missing composite index for order_by")
        docs = [_FakeDoc(k, v) for k, v in self._docs_map.items()]
        return _FakeQuery(docs).order_by(field, direction)

    def limit(self, n):
        docs = [_FakeDoc(k, v) for k, v in self._docs_map.items()]
        return _FakeQuery(docs).limit(n)

    def stream(self):
        return iter([_FakeDoc(k, v) for k, v in self._docs_map.items()])


class _FakeDb:
    def __init__(self, collections=None, fail_order_by=False):
        self.collections = collections or {}
        self.fail_order_by = fail_order_by

    def collection(self, name):
        docs_map = self.collections.get(name, {})
        return _FakeCollection(docs_map, fail_order_by=self.fail_order_by)


class TestAuditAPI(unittest.TestCase):
    def setUp(self):
        self.sample_runs = {
            "audit_1795090543833": {
                "run_id": "audit_1795090543833",
                "trade_date": "2026-11-18",
                "created_at": "2026-11-18T16:45:00-05:00",
                "status": "PASS",
                "inconclusive": False,
                "checks_executed": 14,
                "skipped_checks": {"broker_missing_fill": "No broker executions found"},
                "findings_count": 0,
                "report_markdown": "# Trade Log Audit Report\n\nNo defects found.\n",
            },
            "audit_1795090543800": {
                "run_id": "audit_1795090543800",
                "trade_date": "2026-11-17",
                "created_at": "2026-11-17T16:45:00-05:00",
                "status": "DEFECTS_FOUND",
                "inconclusive": False,
                "checks_executed": 13,
                "skipped_checks": {
                    "broker_missing_fill": "Skipped",
                    "failed_trade_unreplayed": "Spool empty",
                },
                "findings_count": 2,
                "report_markdown": "# Trade Log Audit Report\n\n- Finding 1\n",
            },
        }

        self.sample_findings = {
            "f_pnl_1": {
                "key": "f_pnl_1",
                "check_id": "pnl_mismatch",
                "category": "INTEGRITY",
                "symbol": "NVDA",
                "severity": "critical",
                "status": "open",
                "title": "P&L Mismatch on NVDA",
                "description": "Booked P&L diverged by $1.20",
                "evidence": {"booked": 10.0, "broker": 8.8},
                "run_count": 1,
                "created_at": "2026-11-18T16:45:00",
                "last_seen": "2026-11-18T16:45:00",
            },
            "f_stop_2": {
                "key": "f_stop_2",
                "check_id": "stop_loss_overrun",
                "category": "ANOMALY",
                "symbol": "IWM",
                "severity": "high",
                "status": "recurring",
                "title": "Stop Loss Overrun on IWM",
                "description": "Loss exceeded modeled barrier",
                "evidence": {"loss_pct": -6.2},
                "run_count": 3,
                "created_at": "2026-11-16T16:45:00",
                "last_seen": "2026-11-18T16:45:00",
            },
            "f_hold_3": {
                "key": "f_hold_3",
                "check_id": "hold_time_outlier",
                "category": "ANOMALY",
                "symbol": "CAT",
                "severity": "low",
                "status": "resolved",
                "title": "Hold Time Outlier on CAT",
                "description": "Holding duration resolved",
                "evidence": {},
                "run_count": 2,
                "created_at": "2026-11-15T16:45:00",
                "last_seen": "2026-11-16T16:45:00",
                "resolved_at": "2026-11-17T16:45:00",
            },
            "f_win_4": {
                "key": "f_win_4",
                "check_id": "winner_pattern",
                "category": "GOOD_PRACTICE",
                "symbol": "QQQ",
                "severity": "opportunity",
                "status": "dismissed",
                "title": "Winner Pattern on QQQ",
                "description": "High PF setup",
                "evidence": {},
                "run_count": 1,
                "created_at": "2026-11-14T16:45:00",
                "last_seen": "2026-11-14T16:45:00",
            },
        }

        self.fake_db = _FakeDb(
            collections={
                "audit_runs": self.sample_runs,
                "audit_findings": self.sample_findings,
            }
        )

        self.client_patch = patch("main.firestore.Client", return_value=self.fake_db)
        self.client_patch.start()
        self.db_patch = patch.object(main.dashboard_data, "db", self.fake_db)
        self.db_patch.start()

        self.client = TestClient(main.app)
        main.authenticated_sessions[SESSION_ID] = FUTURE
        self.headers = {"Cookie": f"dashboard_session={SESSION_ID}"}

    def tearDown(self):
        main.authenticated_sessions.pop(SESSION_ID, None)
        self.db_patch.stop()
        self.client_patch.stop()

    def test_auth_gates_on_audit_pages_and_apis(self):
        # Unauthenticated page requests return the login page (HTTP 200 with login form)
        r_page = self.client.get("/audit")
        assert r_page.status_code == 200
        assert "Access Code" in r_page.text or "password" in r_page.text

        r_page_rev = self.client.get("/audit-review")
        assert r_page_rev.status_code == 200
        assert "Access Code" in r_page_rev.text or "password" in r_page_rev.text

        # Unauthenticated API requests return 401
        assert self.client.get("/api/audit/runs").status_code == 401
        assert self.client.get("/api/audit/runs/some_run").status_code == 401
        assert self.client.get("/api/audit/findings").status_code == 401

    def test_audit_review_page_served_when_authenticated(self):
        res = self.client.get("/audit", headers=self.headers)
        assert res.status_code == 200
        assert "Trade Log Audit Review" in res.text
        assert "15 Deterministic Checks" in res.text

        res_rev = self.client.get("/audit-review", headers=self.headers)
        assert res_rev.status_code == 200
        assert "Trade Log Audit Review" in res_rev.text

    def test_audit_review_page_not_found(self):
        with patch("os.path.exists", return_value=False):
            res = self.client.get("/audit", headers=self.headers)
            assert res.status_code == 404
            assert "Audit review page not found" in res.json()["detail"]

    def test_get_audit_runs_success(self):
        res = self.client.get("/api/audit/runs?limit=10", headers=self.headers)
        assert res.status_code == 200
        data = res.json()
        assert "runs" in data
        assert len(data["runs"]) == 2
        # Verify newest run is first
        assert data["runs"][0]["run_id"] == "audit_1795090543833"
        assert data["runs"][0]["trade_date"] == "2026-11-18"
        assert data["runs"][0]["checks_executed"] == 14

    def test_get_audit_runs_limit_clamping(self):
        # limit <= 0 clamped to 1
        res = self.client.get("/api/audit/runs?limit=0", headers=self.headers)
        assert res.status_code == 200
        assert len(res.json()["runs"]) == 1

        # limit > 200 clamped to 200
        res = self.client.get("/api/audit/runs?limit=999", headers=self.headers)
        assert res.status_code == 200
        assert len(res.json()["runs"]) == 2

    def test_get_audit_runs_orderby_fallback(self):
        # Test fallback when order_by fails (e.g., missing index)
        self.fake_db.fail_order_by = True
        res = self.client.get("/api/audit/runs", headers=self.headers)
        assert res.status_code == 200
        runs = res.json()["runs"]
        assert len(runs) == 2
        assert runs[0]["run_id"] == "audit_1795090543833"

    def test_get_audit_runs_error_handling(self):
        with patch.object(self.fake_db, "collection", side_effect=RuntimeError("Firestore connection error")):
            res = self.client.get("/api/audit/runs", headers=self.headers)
            assert res.status_code == 500
            assert "Failed to fetch audit runs" in res.json()["detail"]

    def test_get_audit_run_by_id_success(self):
        res = self.client.get("/api/audit/runs/audit_1795090543833", headers=self.headers)
        assert res.status_code == 200
        data = res.json()
        assert data["run_id"] == "audit_1795090543833"
        assert data["status"] == "PASS"
        assert "report_markdown" in data

    def test_get_audit_run_by_id_not_found(self):
        res = self.client.get("/api/audit/runs/nonexistent_run", headers=self.headers)
        assert res.status_code == 404
        assert "Audit run 'nonexistent_run' not found" in res.json()["detail"]

    def test_get_audit_run_by_id_error_handling(self):
        with patch.object(self.fake_db, "collection", side_effect=RuntimeError("DB explosion")):
            res = self.client.get("/api/audit/runs/audit_1795090543833", headers=self.headers)
            assert res.status_code == 500
            assert "Failed to fetch audit run" in res.json()["detail"]

    def test_get_audit_findings_all(self):
        res = self.client.get("/api/audit/findings?status=all", headers=self.headers)
        assert res.status_code == 200
        findings = res.json()["findings"]
        assert len(findings) == 4
        # Verify severity sort: critical first, then high, then low, then opportunity
        severities = [f["severity"] for f in findings]
        assert severities == ["critical", "high", "low", "opportunity"]

    def test_get_audit_findings_server_side_counts_and_newest_first(self):
        """#155 AUD-011: KPI totals cover all 500 rows, not the 200-row page."""
        base = datetime.datetime(2026, 11, 1, 0, 0, 0)
        docs = {}
        for i in range(500):
            status = "open" if i < 250 else ("resolved" if i < 400 else "dismissed")
            key = f"f_{i:03d}"
            docs[key] = {
                "key": key,
                "status": status,
                "severity": "high",
                "category": "INTEGRITY",
                "symbol": "NVDA",
                "last_seen": (base + datetime.timedelta(minutes=i)).isoformat(),
            }
        self.fake_db.collections["audit_findings"] = docs

        res = self.client.get("/api/audit/findings?limit=200&status=all", headers=self.headers)
        assert res.status_code == 200
        data = res.json()
        assert data["counts"] == {
            "total": 500, "active": 250, "resolved": 150, "dismissed": 100,
        }
        assert (data["total"], data["active"], data["resolved"], data["dismissed"]) == (
            500, 250, 150, 100,
        )
        assert len(data["findings"]) == 200
        keys = [f["key"] for f in data["findings"]]
        assert keys[0] == "f_499"
        assert keys[-1] == "f_300"

    def test_get_audit_findings_filter_status(self):
        # Active status returns open and recurring
        res = self.client.get("/api/audit/findings?status=active", headers=self.headers)
        assert res.status_code == 200
        findings = res.json()["findings"]
        assert len(findings) == 2
        statuses = {f["status"] for f in findings}
        assert statuses == {"open", "recurring"}

        # Specific status: resolved
        res_res = self.client.get("/api/audit/findings?status=resolved", headers=self.headers)
        assert res_res.status_code == 200
        assert len(res_res.json()["findings"]) == 1
        assert res_res.json()["findings"][0]["key"] == "f_hold_3"

        # Specific status: open
        res_open = self.client.get("/api/audit/findings?status=open", headers=self.headers)
        assert res_open.status_code == 200
        assert len(res_open.json()["findings"]) == 1
        assert res_open.json()["findings"][0]["key"] == "f_pnl_1"

    def test_get_audit_findings_filter_category_and_symbol(self):
        # Filter by category ANOMALY
        res = self.client.get("/api/audit/findings?category=ANOMALY&status=all", headers=self.headers)
        assert res.status_code == 200
        findings = res.json()["findings"]
        assert len(findings) == 2
        for f in findings:
            assert f["category"] == "ANOMALY"

        # Filter by symbol NVDA
        res_sym = self.client.get("/api/audit/findings?symbol=NVDA&status=all", headers=self.headers)
        assert res_sym.status_code == 200
        findings_sym = res_sym.json()["findings"]
        assert len(findings_sym) == 1
        assert findings_sym[0]["symbol"] == "NVDA"

        # Filter with category ALL and symbol ALL
        res_all = self.client.get("/api/audit/findings?category=ALL&symbol=ALL&status=all", headers=self.headers)
        assert res_all.status_code == 200
        assert len(res_all.json()["findings"]) == 4

    def test_get_audit_findings_limit_clamping(self):
        res = self.client.get("/api/audit/findings?limit=1&status=all", headers=self.headers)
        assert res.status_code == 200
        assert len(res.json()["findings"]) == 1

        res_zero = self.client.get("/api/audit/findings?limit=0&status=all", headers=self.headers)
        assert res_zero.status_code == 200
        assert len(res_zero.json()["findings"]) == 1

    def test_get_audit_findings_error_handling(self):
        with patch.object(self.fake_db, "collection", side_effect=RuntimeError("Firestore query failed")):
            res = self.client.get("/api/audit/findings", headers=self.headers)
            assert res.status_code == 500
            assert "Failed to fetch audit findings" in res.json()["detail"]

    def test_session_expiry_firestore_delete_exception_branches(self):
        # Test lines 190-191 and 205-206: exception handling during session deletion
        past = datetime.datetime.now() - datetime.timedelta(hours=5)

        # 1. Local expired session with failing sessions_ref.delete()
        bad_sessions_ref = MagicMock()
        bad_sessions_ref.document.return_value.delete.side_effect = RuntimeError("Delete error")
        with patch.object(main, "sessions_ref", bad_sessions_ref):
            main.authenticated_sessions["expired_local"] = past
            assert not main.is_valid_session("expired_local")

        # 2. Firestore expired session with failing sessions_ref.delete()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"expiry": past}
        bad_sessions_ref.document.return_value.get.return_value = mock_doc
        with patch.object(main, "sessions_ref", bad_sessions_ref):
            main.authenticated_sessions.pop("expired_fs", None)
            assert not main.is_valid_session("expired_fs")

    def test_load_bot_parameters_firestore_branches(self):
        # Test lines 444-445 and 447-448 in load_bot_parameters
        with patch("os.path.exists", return_value=False):
            # Test exception in Firestore query (lines 447-448)
            mock_db = MagicMock()
            mock_db.collection.side_effect = RuntimeError("Firestore error")
            with patch.object(main.dashboard_data, "db", mock_db):
                assert main.load_bot_parameters("UNKNOWN_SYM") is None

            # Test successful Firestore query but failure writing local file (lines 444-445)
            mock_doc = MagicMock()
            mock_doc.to_dict.return_value = {"bot_params": {"stop_loss": 0.05}}
            mock_db2 = MagicMock()
            mock_db2.collection.return_value.where.return_value.limit.return_value.stream.return_value = [mock_doc]
            with patch.object(main.dashboard_data, "db", mock_db2):
                with patch("builtins.open", side_effect=PermissionError("Read only")):
                    params = main.load_bot_parameters("PROVISIONED_SYM")
                    assert params == {"stop_loss": 0.05}

    def test_emergency_flatten_missing_json(self):
        # Test lines 3529-3530
        res = self.client.post(
            "/api/bot-control/bots/nvda_bot/emergency-flatten-and-demote",
            content=b"not-json",
            headers={"Content-Type": "application/json", **self.headers}
        )
        assert res.status_code == 400
        assert "Missing JSON body" in res.json()["detail"]

    def test_load_bots_config_stat_oserror(self):
        # Test lines 334-335
        real_exists = os.path.exists
        def fake_exists(p):
            if "bots.json" in str(p):
                return True
            return real_exists(p)

        orig_stat = os.stat
        def fake_stat(path, *a, **k):
            if "bots.json" in str(path):
                raise OSError("Simulated stat failure")
            return orig_stat(path, *a, **k)

        with patch("os.path.exists", side_effect=fake_exists):
            with patch("os.stat", side_effect=fake_stat):
                with patch("builtins.open", unittest.mock.mock_open(read_data='{"bots": []}')):
                    # Invalidate cache to force reload
                    main._bot_config_cache["mtime"] = None
                    cfg = main.load_bot_configs()
                    assert "bots" in cfg

    def test_bot_configs_heartbeat_branches(self):
        # Test lines 1599 and 1604-1605
        bots_data = {
            "bots": [
                {"name": "bot_int_hb", "client_id": 1, "symbol": "NVDA"},
                {"name": "bot_bad_hb", "client_id": 2, "symbol": "IWM"},
            ]
        }
        mock_coll = MagicMock()
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {"client_id": 1, "last_heartbeat": 12345}
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = {"client_id": 2, "last_heartbeat": "not-a-date"}
        mock_coll.stream.return_value = [mock_doc1, mock_doc2]

        with patch("main.load_bot_configs", return_value=bots_data):
            with patch.object(main.dashboard_data, "bot_data_ref", mock_coll):
                res = self.client.get("/api/bot-configs", headers=self.headers)
                assert res.status_code == 200




if __name__ == "__main__":
    unittest.main()
