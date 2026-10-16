"""
Unit tests for the StockPicker Firestore read-modify-write helper.

`stock_picks/current` is mutated by several producers (accept / reject /
provision-bot and the In-Flight Sentinel telemetry mirror). Every transition is
a full read-modify-write of that document, and a bare get()->set() is
last-write-wins across Cloud Run instances. `run_stock_pick_rmw` performs the
read inside a Firestore transaction in production.

These tests exercise BOTH code paths (plain RMW and transactional) using fake
client / transaction doubles, so the production path is verified without
touching the network or a live Firestore project. The fake transaction mirrors
the narrow contract `google.cloud.firestore_v1.transactional` relies on
(_read_only, _max_attempts, _clean_up, _begin, _commit, _rollback, set).
"""

import copy

import pytest
from google.api_core import exceptions as gexc

from stockpicker.firestore_utils import run_stock_pick_rmw, transactions_enabled


class _FakeSnapshot:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        # A real Firestore read round-trips the document and deserialises a
        # fresh structure, so a retry never sees a partially-mutated snapshot.
        # Deep-copy here to reproduce that guarantee.
        return None if self._data is None else copy.deepcopy(self._data)


class _FakeDocRef:
    """Fake DocumentReference: records gets/writes and serves a fixed snapshot."""

    def __init__(self, data, exists=True):
        self._data = data
        self._exists = exists
        self.writes = []
        self.get_calls = []

    def get(self, transaction=None):
        self.get_calls.append(transaction)
        return _FakeSnapshot(self._data, self._exists)

    def set(self, data):
        self.writes.append(data)
        self._data = data


class _FakeTransaction:
    """Minimal stand-in for google.cloud.firestore_v1.Transaction."""

    def __init__(self, commit_failures=0):
        self._read_only = False
        self._max_attempts = max(1, commit_failures + 1)
        self._id = None
        self._write_pbs = []
        self.pending = []
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0
        self.applied_writes = []
        self._commit_failures = commit_failures

    def _clean_up(self):
        self._write_pbs = []
        self.pending = []
        self._id = None

    def _begin(self, retry_id=None):
        self.begins += 1
        self._id = b"fake-txn-%d" % self.begins

    def _rollback(self):
        self.rollbacks += 1
        self._clean_up()

    def _commit(self):
        if self._commit_failures > 0:
            self._commit_failures -= 1
            raise gexc.Aborted("simulated contention")
        self.commits += 1
        self.applied_writes = list(self.pending)
        self._clean_up()

    def set(self, doc_ref, data):
        self.pending.append((doc_ref, data))


class _FakeDB:
    def __init__(self, transaction):
        self._transaction = transaction

    def transaction(self):
        return self._transaction


# --------------------------------------------------------------------------
# Feature detection
# --------------------------------------------------------------------------

def test_transactions_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STOCKPICKS_TXN_RMW", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    assert transactions_enabled() is False


def test_transactions_enabled_on_cloud_run(monkeypatch):
    monkeypatch.delenv("STOCKPICKS_TXN_RMW", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.setenv("K_SERVICE", "tradingbot-dashboard")
    assert transactions_enabled() is True


def test_transactions_enabled_on_cloud_run_job(monkeypatch):
    monkeypatch.delenv("STOCKPICKS_TXN_RMW", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("CLOUD_RUN_JOB", "stockpicker-run")
    assert transactions_enabled() is True


def test_transaction_env_override_wins(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "tradingbot-dashboard")
    monkeypatch.setenv("STOCKPICKS_TXN_RMW", "0")
    assert transactions_enabled() is False

    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.setenv("STOCKPICKS_TXN_RMW", "yes")
    assert transactions_enabled() is True


# --------------------------------------------------------------------------
# Plain (fallback) path
# --------------------------------------------------------------------------

def test_requires_doc_ref():
    with pytest.raises(ValueError):
        run_stock_pick_rmw(lambda d: True, doc_ref=None, transactional=False)


def test_fallback_persists_mutation_via_set():
    doc_ref = _FakeDocRef({"already_accepted": []})

    def mutate(data):
        data["already_accepted"].append({"symbol": "NVDA"})
        return True

    assert run_stock_pick_rmw(mutate, doc_ref=doc_ref, transactional=False) is True
    assert doc_ref.writes == [{"already_accepted": [{"symbol": "NVDA"}]}]
    # Plain path reads WITHOUT a transaction and never opens one.
    assert doc_ref.get_calls == [None]


def test_fallback_skips_write_when_mutation_returns_false():
    doc_ref = _FakeDocRef({"x": 1})
    assert run_stock_pick_rmw(lambda d: False, doc_ref=doc_ref, transactional=False) is False
    assert doc_ref.writes == []


def test_fallback_missing_document_is_noop():
    doc_ref = _FakeDocRef(None, exists=False)
    assert run_stock_pick_rmw(lambda d: True, doc_ref=doc_ref, transactional=False) is False
    assert doc_ref.writes == []


def test_fallback_create_if_missing_writes_new_document():
    doc_ref = _FakeDocRef(None, exists=False)

    def mutate(data):
        assert data == {}
        data["status"] = "success"
        return True

    assert run_stock_pick_rmw(mutate, doc_ref=doc_ref, transactional=False, create_if_missing=True) is True
    assert doc_ref.writes == [{"status": "success"}]


def test_transactional_requested_without_db_falls_back():
    """No client => cannot transact; must degrade to the plain path, not crash."""
    doc_ref = _FakeDocRef({"already_accepted": []})

    def mutate(data):
        data["already_accepted"].append({"symbol": "AAPL"})
        return True

    assert run_stock_pick_rmw(mutate, doc_ref=doc_ref, db=None, transactional=True) is True
    assert doc_ref.writes == [{"already_accepted": [{"symbol": "AAPL"}]}]


# --------------------------------------------------------------------------
# Transactional path
# --------------------------------------------------------------------------

def test_transactional_path_reads_and_writes_in_one_transaction():
    doc_ref = _FakeDocRef({"already_accepted": []})
    txn = _FakeTransaction()
    db = _FakeDB(txn)

    def mutate(data):
        data["already_accepted"].append({"symbol": "AAPL"})
        return True

    assert run_stock_pick_rmw(mutate, doc_ref=doc_ref, db=db, transactional=True) is True
    assert txn.begins == 1
    assert txn.commits == 1
    # The read carried the transaction, and the write went THROUGH it.
    assert doc_ref.get_calls == [txn]
    assert doc_ref.writes == []  # direct set() must not be used on this path
    assert txn.applied_writes == [
        (doc_ref, {"already_accepted": [{"symbol": "AAPL"}]})
    ]


def test_transactional_path_skips_write_on_false():
    doc_ref = _FakeDocRef({"a": 1})
    txn = _FakeTransaction()
    assert run_stock_pick_rmw(lambda d: False, doc_ref=doc_ref, db=_FakeDB(txn), transactional=True) is False
    assert txn.applied_writes == []
    assert doc_ref.writes == []


def test_transactional_missing_document_is_noop():
    doc_ref = _FakeDocRef(None, exists=False)
    txn = _FakeTransaction()
    assert run_stock_pick_rmw(lambda d: True, doc_ref=doc_ref, db=_FakeDB(txn), transactional=True) is False
    assert txn.applied_writes == []


def test_transactional_create_if_missing_writes_new_document():
    doc_ref = _FakeDocRef(None, exists=False)
    txn = _FakeTransaction()

    def mutate(data):
        assert data == {}
        data["status"] = "success"
        return True

    assert run_stock_pick_rmw(
        mutate, doc_ref=doc_ref, db=_FakeDB(txn), transactional=True, create_if_missing=True
    ) is True
    assert txn.applied_writes == [(doc_ref, {"status": "success"})]


def test_transaction_retries_on_contention_and_applies_once():
    """An Aborted commit retries the whole mutation against a fresh snapshot."""
    doc_ref = _FakeDocRef({"already_accepted": []})
    txn = _FakeTransaction(commit_failures=1)
    seen = []

    def mutate(data):
        seen.append(dict(data))
        data["already_accepted"].append({"symbol": "MSFT"})
        return True

    assert run_stock_pick_rmw(mutate, doc_ref=doc_ref, db=_FakeDB(txn), transactional=True) is True

    assert txn.begins == 2          # first attempt + one retry
    assert txn.commits == 1
    assert len(seen) == 2           # mutation re-run on the fresh snapshot
    # Exactly one write is applied, and it is the retry's result (not doubled).
    assert txn.applied_writes == [(doc_ref, {"already_accepted": [{"symbol": "MSFT"}]})]
    assert doc_ref.writes == []
