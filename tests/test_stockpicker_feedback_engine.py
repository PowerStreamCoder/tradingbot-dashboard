"""
Unit tests for StockPicker Phase 7 Closed-Loop Feedback Engine (feedback_engine.py).

Verifies:
1. Immutable snapshot archiving per pick (with factor sub-scores, entry price, dossier)
2. Forward alpha computation (t+1, t+5, t+20 excess return vs benchmark)
3. Spearman rank correlation calculation (pure Python implementation)
4. Factor IC calculation and ledger creation with dynamic weight adaptations (±20% limits)
5. Weight adjustments loading and stale data handling (> 7 days)
6. Bot execution feedback ingestion and archive snapshot linking
7. Execution statistics aggregation (win rate, Sharpe approximation, stop-out rates)
8. Rejection pattern analysis and screener auto-tuning suggestions (> 40% threshold)
9. 90-day archive TTL cleanup
10. Full feedback loop runner orchestration
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from stockpicker.feedback_engine import (
    archive_pick_snapshot,
    archive_run_snapshots,
    compute_forward_alpha,
    compute_factor_ic,
    load_weight_adjustments,
    ingest_execution_feedback,
    compute_execution_summary,
    analyze_rejection_patterns,
    cleanup_stale_archives,
    run_feedback_loop,
    _spearman_rank_correlation,
    _extract_factor_scores,
)


def test_spearman_rank_correlation():
    """Verify Spearman rank correlation logic for monotonicity and edge cases."""
    # Perfect positive correlation
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert pytest.approx(_spearman_rank_correlation(x, y), 0.001) == 1.0

    # Perfect negative correlation
    y_neg = [50.0, 40.0, 30.0, 20.0, 10.0]
    assert pytest.approx(_spearman_rank_correlation(x, y_neg), 0.001) == -1.0

    # Tied values handling
    x_ties = [1.0, 2.0, 2.0, 4.0]
    y_ties = [10.0, 20.0, 20.0, 40.0]
    assert _spearman_rank_correlation(x_ties, y_ties) > 0.9

    # Edge cases: identical lists or small lists (< 2)
    assert _spearman_rank_correlation([1.0], [1.0]) == 0.0
    assert _spearman_rank_correlation([], []) == 0.0


def test_extract_factor_scores():
    """Verify regex extraction of factor sub-scores from fundamental_reasons string."""
    candidate = {
        "revenue_yoy": 0.25,
        "operating_margin": 0.15,
        "gross_margin": 0.40,
        "debt_to_equity": 80.0,
        "current_ratio": 1.5,
        "eps_surprise": 0.12,
        "recommendation_mean": 1.8,
        # Exactly the magnitudes/shapes emitted by core.get_insider_transactions
        # and alternative_data_client - there is deliberately no fabricated
        # "insider_score=..." token, because no producer ever emits one.
        "fundamental_reasons": (
            "rel_vol=2.10x (+4.0); 1d_price=+3.5% (+35.0); analyst=1.8 (+20.0); "
            "growth_quality=25.0 (+12.5); persistence=uptrend (+15.0); "
            "cluster_buying: 1,250,000 shares ($5.0M, 2 buyers) (+12.0); "
            "congress_buys=3 trades (2 members) (+5.0)"
        )
    }
    extracted = _extract_factor_scores(candidate)

    assert extracted["revenue_yoy"] == 0.25
    assert extracted["operating_margin"] == 0.15
    assert extracted["relative_volume"] == 4.0
    assert extracted["price_momentum_1d"] == 35.0
    assert extracted["analyst_views"] == 20.0
    assert extracted["growth_quality"] == 12.5
    assert extracted["persistence"] == 15.0
    assert extracted["insider_buys"] == 12.0
    # Regression for the greedy-regex fix: the insider pattern must stop at the
    # "; " separator instead of swallowing the congress factor that follows it.
    assert extracted["congress_buys"] == 5.0


def test_archive_pick_snapshot():
    """Verify creation of immutable snapshot document for a pick."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc

    candidate = {
        "ticker": "KTOS",
        "composite_score": 88.5,
        "fundamental_score": 75.0,
        "explosiveness": 8.5,
        "current_price": 28.50,
        "strategy_track": "GROWTH",
        "status": "PENDING_REVIEW",
        "revenue_yoy": 0.22,
        "catalyst": "Defense contract win",
        "evidence_dossier": {
            "bull_drivers": ["Contract award", "Growing backlog"],
            "risk_warnings": ["Macro volatility"]
        }
    }

    pick_id = archive_pick_snapshot(mock_db, candidate, "2026-10-05")
    assert pick_id == "2026-10-05_KTOS"

    mock_db.collection.assert_called_with("stock_picks_archive")
    mock_db.collection().document.assert_called_with("2026-10-05_KTOS")

    # Verify document payload saved
    saved_snapshot = mock_doc.set.call_args[0][0]
    assert saved_snapshot["pick_id"] == "2026-10-05_KTOS"
    assert saved_snapshot["symbol"] == "KTOS"
    assert saved_snapshot["composite_score"] == 88.5
    assert saved_snapshot["entry_price"] == 28.50
    assert saved_snapshot["execution_linked"] is False
    assert len(saved_snapshot["bull_drivers"]) == 2


def test_archive_run_snapshots():
    """Verify batch snapshot archiving of actionable leads."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc

    leads = [
        {"ticker": "KTOS", "composite_score": 85.0},
        {"ticker": "NVDA", "composite_score": 90.0},
    ]

    archived_ids = archive_run_snapshots(mock_db, leads, "2026-10-05")
    assert len(archived_ids) == 2
    assert "2026-10-05_KTOS" in archived_ids
    assert "2026-10-05_NVDA" in archived_ids


@patch("stockpicker.feedback_engine._fetch_historical_price")
def test_compute_forward_alpha(mock_hist):
    """Each horizon is measured at its OWN target date (no mislabeling).

    Regression: the previous implementation stamped the *same* return into
    every open horizon once enough days had elapsed, so alpha_t1 == alpha_t5.
    """
    from datetime import datetime, timedelta, timezone

    run_dt = datetime.now(timezone.utc) - timedelta(days=15)
    run_date_str = run_dt.date().isoformat()
    d1 = (run_dt + timedelta(days=1)).date().isoformat()
    d5 = (run_dt + timedelta(days=5)).date().isoformat()

    # SPY entry = 500; +1d = 505 (+1.0%); +5d = 510 (+2.0%)
    # KTOS entry = 30; +1d = 31 (+3.333%); +5d = 33 (+10.0%)
    price_by_key = {
        ("SPY", run_date_str): 500.0,
        ("SPY", d1): 505.0,
        ("SPY", d5): 510.0,
        ("KTOS", d1): 31.0,
        ("KTOS", d5): 33.0,
    }
    mock_hist.side_effect = lambda ticker, date_str: price_by_key.get((ticker, date_str))

    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.id = f"{run_date_str}_KTOS"
    mock_doc.to_dict.return_value = {
        "symbol": "KTOS",
        "ticker": "KTOS",
        "run_date": run_date_str,
        "entry_price": 30.0,
        "alpha_t1": None,
        "alpha_t5": None,
        "alpha_t20": None,
        "forward_prices": {},
    }

    mock_query = MagicMock()
    mock_query.stream.return_value = [mock_doc]
    mock_db.collection.return_value.where.return_value = mock_query

    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    summary = compute_forward_alpha(mock_db, max_lookback_days=30)

    assert summary["picks_evaluated"] == 1
    assert summary["alphas_updated"] == 1

    # Verify update payload on document reference
    mock_doc_ref.update.assert_called_once()
    update_data = mock_doc_ref.update.call_args[0][0]

    # t+1: +3.333% minus +1.0% = +2.333%
    assert update_data["alpha_t1"] == pytest.approx((31.0 - 30.0) / 30.0 - (505.0 - 500.0) / 500.0, abs=1e-6)
    # t+5: +10.0% minus +2.0% = +8.0%
    assert update_data["alpha_t5"] == pytest.approx(0.08, abs=1e-6)
    # t+20 not reached yet (15 days elapsed) -> must NOT be stamped
    assert "alpha_t20" not in update_data
    assert update_data["forward_prices"] == {"t1d": 31.0, "t5d": 33.0}


def test_compute_factor_ic_ledger_and_weight_adaptation():
    """Verify Factor IC calculation and resulting weight adjustments within ±20% bounds."""
    mock_db = MagicMock()

    mock_docs = []
    data_points = [
        {"factor": 10.0, "alpha": 0.02},
        {"factor": 20.0, "alpha": 0.04},
        {"factor": 30.0, "alpha": 0.06},
        {"factor": 40.0, "alpha": 0.08},
        {"factor": 50.0, "alpha": 0.10},
    ]

    for i, pt in enumerate(data_points):
        doc = MagicMock()
        doc.to_dict.return_value = {
            "symbol": f"SYM{i}",
            "alpha_t5": pt["alpha"],
            "factor_scores": {
                "price_momentum_1d": pt["factor"],
                "relative_volume": 10.0 - pt["factor"],
            }
        }
        mock_docs.append(doc)

    mock_db.collection.return_value.where.return_value.stream.return_value = mock_docs

    ledger = compute_factor_ic(mock_db, lookback_days=60, alpha_horizon=5)

    assert ledger["sample_size"] == 5
    assert "price_momentum_1d" in ledger["factor_ics"]
    assert "relative_volume" in ledger["factor_ics"]

    ic_mom = ledger["factor_ics"]["price_momentum_1d"]["ic"]
    assert ic_mom > 0.9
    adj_mom = ledger["weight_adjustments"]["price_momentum_1d"]
    assert adj_mom["direction"] == "BOOST"
    assert adj_mom["magnitude"] <= 0.20

    ic_vol = ledger["factor_ics"]["relative_volume"]["ic"]
    assert ic_vol < -0.9
    adj_vol = ledger["weight_adjustments"]["relative_volume"]
    assert adj_vol["direction"] == "DECAY"
    assert adj_vol["magnitude"] <= 0.20


def test_load_weight_adjustments():
    """Verify loading multipliers and stale date fallback."""
    mock_db = MagicMock()
    mock_doc = MagicMock()

    now_iso = datetime.now(timezone.utc).isoformat()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "computed_at": now_iso,
        "weight_adjustments": {
            "price_momentum_1d": {"direction": "BOOST", "magnitude": 0.15},
            "relative_volume": {"direction": "DECAY", "magnitude": 0.10},
            "growth_quality": {"direction": "HOLD", "magnitude": 0.0},
        }
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    multipliers = load_weight_adjustments(mock_db)
    assert pytest.approx(multipliers["price_momentum_1d"], 0.01) == 1.15
    assert pytest.approx(multipliers["relative_volume"], 0.01) == 0.90
    assert pytest.approx(multipliers["growth_quality"], 0.01) == 1.00

    stale_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    mock_doc.to_dict.return_value["computed_at"] = stale_iso
    stale_multipliers = load_weight_adjustments(mock_db)
    assert stale_multipliers == {}


def test_ingest_execution_feedback():
    """Verify recording of trade execution outcome and linking to archive snapshot."""
    mock_db = MagicMock()
    mock_archive_doc = MagicMock()
    mock_archive_doc.exists = True
    mock_db.collection.return_value.document.return_value.get.return_value = mock_archive_doc

    res = ingest_execution_feedback(
        db=mock_db,
        pick_id="2026-10-01_KTOS",
        realized_pnl=420.50,
        trade_duration_days=8,
        exit_reason="profit_target_hit",
        execution_mode="LIVE"
    )

    assert res["status"] == "success"
    assert res["pick_id"] == "2026-10-01_KTOS"

    mock_db.collection.assert_any_call("stockpicker_execution_feedback")


def test_compute_execution_summary():
    """Verify execution metrics aggregation (win rate, Sharpe, sector stop-outs)."""
    mock_db = MagicMock()

    docs = []
    trade_data = [
        {"pnl": 500.0, "exit": "profit_target_hit", "track": "GROWTH", "sector": "Aerospace"},
        {"pnl": -150.0, "exit": "atr_stop_loss", "track": "GROWTH", "sector": "Aerospace"},
        {"pnl": 200.0, "exit": "option_expiration", "track": "INCOME", "sector": "Tech"},
        {"pnl": 350.0, "exit": "profit_target_hit", "track": "GROWTH", "sector": "Bio"},
    ]
    for td in trade_data:
        d = MagicMock()
        d.to_dict.return_value = {
            "realized_pnl": td["pnl"],
            "exit_reason": td["exit"],
            "metadata": {"strategy_track": td["track"], "sector": td["sector"]}
        }
        docs.append(d)

    mock_db.collection.return_value.where.return_value.stream.return_value = docs

    summary = compute_execution_summary(mock_db, lookback_days=90)
    assert summary["status"] == "success"
    assert summary["overall"]["total_trades"] == 4
    assert summary["overall"]["win_rate"] == 0.75
    assert summary["overall"]["total_pnl"] == 900.0
    assert summary["overall"]["sharpe_approx"] is not None
    assert "profit_target_hit" in summary["exit_breakdown"]
    assert summary["exit_breakdown"]["profit_target_hit"]["count"] == 2

    # Per-track split comes from feedback metadata {strategy_track}
    assert summary["by_track"]["GROWTH"]["count"] == 3
    assert summary["by_track"]["INCOME"]["count"] == 1

    # Sector-level attribution comes from feedback metadata {sector}
    assert summary["sector_stop_out_rates"]["Aerospace"] == 0.5
    assert summary["sector_stop_out_rates"]["Tech"] == 0.0


def test_analyze_rejection_patterns():
    """Reject reasons come from the current reject modal (templates/bot_focus.html)
    and must map to the matching screener boundary (Loop A / design doc 7.2)."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "rejected_cooldown": [
            {"symbol": "AAA", "rejection_reason": "VALUATION_EXCESSIVE"},
            {"symbol": "BBB", "rejection_reason": "VALUATION_EXCESSIVE"},
            {"symbol": "CCC", "rejection_reason": "VALUATION_EXCESSIVE"},
            {"symbol": "DDD", "rejection_reason": "VOLATILITY_MISALIGNMENT"},
            {"symbol": "EEE", "rejection_reason": "OPERATOR_DISCRETION"},
        ]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    analysis = analyze_rejection_patterns(mock_db)
    assert analysis["total_rejections"] == 5
    assert analysis["reason_patterns"]["VALUATION_EXCESSIVE"]["count"] == 3
    assert analysis["reason_patterns"]["OPERATOR_DISCRETION"]["count"] == 1

    recs = analysis["recommended_filter_adjustments"]
    # Only the 60% VALUATION_EXCESSIVE reason clears the >40% bar.
    assert len(recs) == 1
    assert recs[0]["filter"] == "max_valuation_multiple"
    assert recs[0]["action"] == "tighten"
    assert recs[0]["rejection_rate"] == 0.6


def test_operator_discretion_never_auto_tunes():
    """OPERATOR_DISCRETION is not a tunable screener boundary: even when it is
    the dominant reason it must not emit a filter adjustment."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "rejected_cooldown": [
            {"rejection_reason": "OPERATOR_DISCRETION"},
            {"rejection_reason": "OPERATOR_DISCRETION"},
            {"rejection_reason": "OPERATOR_DISCRETION"},
        ]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    analysis = analyze_rejection_patterns(mock_db)
    assert analysis["total_rejections"] == 3
    assert analysis["recommended_filter_adjustments"] == []


def test_legacy_rejection_vocab_still_maps():
    """Historical records predate the current reject modal; the legacy tags must
    still resolve to a boundary so old feedback remains actionable."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "rejected_cooldown": [{"rejection_reason": "HIGH_DEBT"} for _ in range(3)]
    }
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

    analysis = analyze_rejection_patterns(mock_db)
    filters = {a["filter"] for a in analysis["recommended_filter_adjustments"]}
    assert "max_debt_to_equity" in filters


def test_cleanup_stale_archives():
    """Verify purging of archive snapshots older than 90 days."""
    mock_db = MagicMock()
    old_doc1 = MagicMock()
    old_doc1.id = "2026-05-01_ABC"
    old_doc2 = MagicMock()
    old_doc2.id = "2026-05-01_XYZ"

    def mock_collection(name):
        col = MagicMock()
        if name == "stock_picks_archive":
            col.where.return_value.limit.return_value.stream.return_value = [old_doc1, old_doc2]
        else:
            col.where.return_value.limit.return_value.stream.return_value = []
            col.stream.return_value = []
        return col

    mock_db.collection.side_effect = mock_collection

    res = cleanup_stale_archives(mock_db, ttl_days=90)
    assert res["status"] == "success"
    assert res["total_deleted"] >= 2
    old_doc1.reference.delete.assert_called_once()
    old_doc2.reference.delete.assert_called_once()


def test_run_feedback_loop_orchestration():
    """Verify execution of full feedback loop pipeline."""
    mock_db = MagicMock()

    with patch("stockpicker.feedback_engine.compute_forward_alpha") as mock_alpha, \
         patch("stockpicker.feedback_engine.compute_factor_ic") as mock_ic, \
         patch("stockpicker.feedback_engine.compute_execution_summary") as mock_exec, \
         patch("stockpicker.feedback_engine.analyze_rejection_patterns") as mock_rej, \
         patch("stockpicker.feedback_engine.cleanup_stale_archives") as mock_clean:

        mock_alpha.return_value = {"status": "success", "alphas_updated": 3}
        mock_ic.return_value = {"status": "success", "sample_size": 10}
        mock_exec.return_value = {"status": "success", "total_trades": 5}
        mock_rej.return_value = {"status": "success", "total_rejections": 4}
        mock_clean.return_value = {"status": "success", "total_deleted": 0}

        result = run_feedback_loop(mock_db)

        assert "run_at" in result
        assert "alpha_audit" in result["components"]
        assert "factor_ic" in result["components"]
        assert "execution_summary" in result["components"]
        assert "rejection_analysis" in result["components"]
        assert "cleanup" in result["components"]
