"""#155 AUD-025: end-to-end auditor wiring smoke test, no network and no VM.

Import-only checks cannot catch a broken hand-off between the auditor's run
write, the Firestore read path, the dashboard API/page and the alert receipt.
This walks the real code in that order against one in-memory Firestore double:

    AuditMemory.save_run -> audit_runs mock -> /api/audit/runs -> /audit KPI
    -> notification receipt recorded on the run

The auditor package comes from the sibling tools checkout (added to the
dashboard CI workflow); nothing connects to Firestore, SMTP or the VM.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
_AUDITOR = next(
    (p for p in (ROOT / "tradingbot-tools", ROOT.parent / "tradingbot-tools")
     if (p / "auditor" / "__init__.py").exists()),
    None,
)
if _AUDITOR is None:
    pytest.skip("tradingbot-tools checkout is required for the auditor smoke test",
                allow_module_level=True)
sys.path.insert(0, str(_AUDITOR))

import main  # noqa: E402
from auditor.memory import AuditMemory  # noqa: E402
from auditor.models import AuditRun, CheckStat, Finding  # noqa: E402
from auditor.notify import AlertMemory, dispatch  # noqa: E402

DAY = "2026-11-24"
RUN_ID = "audit_smoke_155"
KEY = "pnl_mismatch|NVDA|2026-11-24"


class _Snapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = dict(data)
        self.exists = bool(data)

    def to_dict(self):
        return dict(self._data)


class _DocRef:
    def __init__(self, store, doc_id):
        self._store = store
        self.id = doc_id

    def set(self, payload):
        self._store[self.id] = dict(payload)

    def get(self):
        return _Snapshot(self.id, self._store[self.id]) if self.id in self._store else _Snapshot(self.id, {})


class _Query:
    def __init__(self, store, field=None, descending=False, cap=None):
        self._store = store
        self._field = field
        self._descending = descending
        self._cap = cap

    def order_by(self, field, direction=None):
        return _Query(self._store, field, str(direction).upper() == "DESC", self._cap)

    def limit(self, n):
        return _Query(self._store, self._field, self._descending, n)

    def stream(self):
        docs = [_Snapshot(doc_id, data) for doc_id, data in self._store.items()]
        if self._field:
            docs.sort(key=lambda d: str(d.to_dict().get(self._field, "")), reverse=self._descending)
        return iter(docs[:self._cap] if self._cap is not None else docs)


class _Collection:
    def __init__(self, store):
        self._store = store

    def document(self, doc_id):
        return _DocRef(self._store, str(doc_id))

    def stream(self):
        return _Query(self._store).stream()

    def order_by(self, field, direction=None):
        return _Query(self._store, field, str(direction).upper() == "DESC")


class _FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return _Collection(self.collections.setdefault(name, {}))


class _Manager:
    """NotificationManager double: accepts only forced synchronous delivery."""

    def __init__(self):
        self.calls = []

    def send_notification(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["sync"] is True
        assert kwargs["force_immediate"] is True
        return {"status": "delivered", "delivered": True,
                "delivered_at": "2026-11-24T21:05:00+00:00"}


def test_auditor_run_reaches_dashboard_and_alert_receipt(tmp_path):
    fake = _FakeFirestore()
    memory = AuditMemory(db=fake, memory_path=tmp_path / "memory.json")
    finding = Finding(
        check_id="pnl_mismatch", category="INTEGRITY", severity="critical",
        title="P&L mismatch", description="Bot gross diverges from IBKR",
        symbol="NVDA", signature=KEY,
    )
    memory.reconcile([finding], DAY)
    memory.save()  # audit_findings for the dashboard KPI

    run = AuditRun(
        run_id=RUN_ID, trade_date=DAY, symbol="NVDA",
        created_at="2026-11-24T21:00:00+00:00",
        findings=[finding], check_stats=[CheckStat(check_id="pnl_mismatch", fired=1, runs=1)],
    )
    run_doc = run.to_dict()

    # The alert is a synchronous page with a durable receipt, mirrored on the run.
    alert = {"dedup_key": KEY, "title": finding.title, "message": finding.description,
             "type": "trade_log_anomaly", "priority": "critical"}
    manager = _Manager()
    alert_memory = AlertMemory(path=tmp_path / "alerts.jsonl")
    dispatched = dispatch([alert], memory=alert_memory, manager=manager)
    assert dispatched["sent"] == 1
    assert dispatched["delivered"] == [KEY]
    assert manager.calls[0]["dedup_key"] == KEY
    run_doc["dispatch"] = {"delivered": dispatched["delivered"],
                           "delivered_at": dispatched["delivered_at"]}
    run_doc["alerted_delivered"] = dispatched["delivered"]
    memory.save_run(run_doc)  # audit_runs for the dashboard API

    # A fresh auditor host can read both collections back from the mock.
    fresh = AuditMemory(db=fake, memory_path=tmp_path / "fresh.json")
    assert RUN_ID in {str(r.get("run_id")) for r in fresh.runs}
    assert finding.key in fresh.records
    assert KEY in fresh._alerted

    session = "smoke-155"
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    main.authenticated_sessions[session] = future
    headers = {"Cookie": f"dashboard_session={session}"}
    try:
        with patch("main.firestore.Client", return_value=fake), \
             patch.object(main.dashboard_data, "db", fake):
            client = TestClient(main.app)

            runs = client.get("/api/audit/runs?limit=50", headers=headers)
            assert runs.status_code == 200
            listed = runs.json()["runs"]
            assert listed[0]["run_id"] == RUN_ID
            assert listed[0]["trade_date"] == DAY
            assert listed[0]["status"] == run.status
            assert listed[0]["alerted_delivered"] == [KEY]

            page = client.get("/audit", headers=headers)
            assert page.status_code == 200
            assert "/static/js/audit_review.js" in page.text

            # /audit KPI cards are fed by this endpoint and its whole-collection counts.
            findings = client.get("/api/audit/findings?status=all", headers=headers)
            assert findings.status_code == 200
            assert findings.json()["counts"] == {"total": 1, "active": 1,
                                                  "resolved": 0, "dismissed": 0}
            assert findings.json()["findings"][0]["key"] == finding.key

            detail = client.get(f"/api/audit/runs/{RUN_ID}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["run_id"] == RUN_ID
    finally:
        main.authenticated_sessions.pop(session, None)

    js = (ROOT / "static" / "js" / "audit_review.js").read_text(encoding="utf-8")
    assert "/api/audit/runs?limit=50" in js
    assert "/api/audit/findings?limit=200" in js
    assert "allFindingCounts" in js

    # A retried page must not re-append a receipt for the same finding.
    retry = dispatch([alert], memory=alert_memory, manager=manager)
    assert retry["sent"] == 0 and retry["skipped"] == 1
    assert len(alert_memory.sent()) == 1
