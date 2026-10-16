"""
Shared Firestore read-modify-write helper for StockPicker HITL state.

`stock_picks/current` stores the human-in-the-loop workflow as JSON arrays
(`already_accepted`, `rejected_cooldown`). Firestore has no partial-array
update, so every producer performs a full read-modify-write of that document:

* the HITL transitions: POST /api/stock-picks/{accept,reject,provision-bot}
* the In-Flight Sentinel telemetry mirror
* the screener run, which replaces the document but merges mid-scan HITL
  decisions (``create_if_missing=True``)

A bare `get()` -> `set()` is last-write-wins: two writers on *different* Cloud
Run instances can read the same snapshot and the later write silently discards
the earlier one. (Inside a single instance the asyncio event loop already
serialises these awaited handlers, so the residual window is cross-instance.)

`run_stock_pick_rmw` closes that window by reading the document *inside* a
Firestore transaction, applying the caller's mutation, and committing the write
atomically. Firestore retries the whole transaction on contention, re-running
the mutation against a fresh snapshot each time.

Testability: a live transaction needs a real client, and the unit suite patches
the document reference rather than the client. The helper therefore falls back
to a plain read-modify-write unless a transaction is explicitly enabled -- which
happens automatically on Cloud Run (`K_SERVICE` / `CLOUD_RUN_JOB`) or when
`STOCKPICKS_TXN_RMW` is truthy. Both the fallback and the transactional code
paths are exercised by unit tests using fake client/transaction doubles, so the
production path is covered without touching the network.
"""

import logging
import os
from typing import Any, Callable, Optional

from google.cloud import firestore

logger = logging.getLogger(__name__)


def transactions_enabled() -> bool:
    """Whether transactional read-modify-write should be used.

    Enabled automatically on Google Cloud compute (Cloud Run services inject
    ``K_SERVICE``; Cloud Run jobs inject ``CLOUD_RUN_JOB``) and can be forced or
    disabled via the ``STOCKPICKS_TXN_RMW`` environment variable
    (``1``/``true``/``yes``/``on`` to force on, anything else to force off).
    Tests leave all of these unset so they take the deterministic fallback path.
    """
    raw = os.environ.get("STOCKPICKS_TXN_RMW")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))


def run_stock_pick_rmw(
    mutate_fn: Callable[[dict], bool],
    *,
    doc_ref: Any,
    db: Any = None,
    transactional: Optional[bool] = None,
    create_if_missing: bool = False,
) -> bool:
    """Atomically read-modify-write a StockPicker Firestore document.

    Args:
        mutate_fn: ``f(data) -> bool``. Receives the freshly-read document dict
            and mutates it **in place**; return ``True`` to persist the change,
            ``False`` to leave the document untouched. It must be pure/idempotent
            because a transaction contention retry re-invokes it against a fresh
            snapshot.
        doc_ref: Document reference to read and write. Required.
        db: Firestore client used to open a transaction. Only needed on the
            transactional path.
        transactional: Force the transactional path on/off. ``None`` (default)
            defers to :func:`transactions_enabled`.
        create_if_missing: When ``True``, a missing document is passed to
            ``mutate_fn`` as ``{}`` and written, instead of aborting the RMW.
            Used by the screener's create-or-replace write; the HITL transitions
            leave it ``False`` so a missing document can surface as a 404.

    Returns:
        bool: ``True`` when a write was applied; ``False`` when the document did
        not exist (and ``create_if_missing`` is ``False``) or ``mutate_fn``
        returned ``False``.
    """
    if doc_ref is None:
        raise ValueError("run_stock_pick_rmw requires a doc_ref")

    if transactional is None:
        transactional = transactions_enabled()
    transactional = bool(transactional and db is not None)

    if not transactional:
        snapshot = doc_ref.get()
        if not snapshot.exists:
            if not create_if_missing:
                return False
            data = {}
        else:
            data = snapshot.to_dict() or {}
        if not mutate_fn(data):
            return False
        doc_ref.set(data)
        return True

    transaction = db.transaction()

    @firestore.transactional
    def _txn(txn):
        snapshot = doc_ref.get(transaction=txn)
        if not snapshot.exists:
            if not create_if_missing:
                return False
            data = {}
        else:
            data = snapshot.to_dict() or {}
        if not mutate_fn(data):
            return False
        txn.set(doc_ref, data)
        return True

    return bool(_txn(transaction))
