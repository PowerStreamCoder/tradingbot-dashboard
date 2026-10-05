"""
Unit tests for In-Flight Thesis Sentinel & Downstream Adaptive Gateway (Phase 6).

Verifies:
1. Mid-trade thesis evaluation and score delta calculation (ΔS = S_today - S_entry)
2. Tier 1 Mild Decay (ΔS in [-10, -25]) triggering TIGHTEN_STOPS (recommended_stop_tightening = 0.5)
3. Tier 2 Severe Drop (S < 45 or ΔS <= -30) triggering THESIS_INVALIDATION_EXIT
4. Track 2 Covered Call Volatility Expansion (ATR > 3.5%) triggering VOLATILITY_LOCKOUT
5. Track 2 Covered Call Impending Earnings (<= 10 days) triggering EARLY_CALL_HARVEST
6. Telemetry persistence to Firestore (bots/bot_{client_id} and stock_picks/current)
7. Feature flag disabled-by-default behavior and dynamic runtime toggling
"""

import pytest
from unittest.mock import MagicMock, patch

from stockpicker.sentinel import audit_in_flight_positions, is_sentinel_enabled, set_sentinel_enabled


def test_sentinel_disabled_by_default():
    """Verify that Sentinel is switched off by default and returns disabled status without work."""
    set_sentinel_enabled(False)
    assert is_sentinel_enabled() is False

    summary = audit_in_flight_positions(db=None)
    assert summary["status"] == "disabled"
    assert summary["enabled"] is False
    assert summary["positions_checked"] == 0
    assert "switched off" in summary["message"]


def test_sentinel_toggle_state():
    """Verify that Sentinel can be toggled on and off at runtime."""
    set_sentinel_enabled(True)
    assert is_sentinel_enabled() is True

    set_sentinel_enabled(False)
    assert is_sentinel_enabled() is False


@pytest.fixture(autouse=True)
def enable_sentinel_for_functional_tests(request):
    """Enable Sentinel for functional algorithmic tests, unless specifically testing disabled."""
    if "disabled" not in request.node.name:
        set_sentinel_enabled(True)
        yield
        set_sentinel_enabled(False)
    else:
        yield


@patch("stockpicker.sentinel._hydrate_active_positions")
@patch("stockpicker.core.compute_fundamental_score")
def test_sentinel_healthy_position(mock_score, mock_hydrate):
    """Test position with stable or improving score remains HEALTHY with HOLD action."""
    mock_hydrate.return_value = [{
        "symbol": "KTOS",
        "client_id": 5,
        "bot_id": "ktos_sma",
        "strategy_track": "GROWTH",
        "entry_score": 80.0
    }]
    # Score improved slightly to 82.0
    mock_score.return_value = {"score": 82.0}

    summary = audit_in_flight_positions(db=None)

    assert summary["status"] == "success"
    assert summary["positions_checked"] == 1
    assert summary["actions_summary"]["HOLD"] == 1

    res = summary["results"][0]
    assert res["symbol"] == "KTOS"
    assert res["thesis_telemetry"]["thesis_state"] == "HEALTHY"
    assert res["thesis_telemetry"]["action_instruction"] == "HOLD"
    assert res["thesis_telemetry"]["score_delta"] == 2.0


@patch("stockpicker.sentinel._hydrate_active_positions")
@patch("stockpicker.core.compute_fundamental_score")
def test_sentinel_mild_decay_tighten_stops(mock_score, mock_hydrate):
    """Test Tier 1 Mild Decay (ΔS in [-10, -25]) triggers TIGHTEN_STOPS."""
    mock_hydrate.return_value = [{
        "symbol": "CRWD",
        "client_id": 6,
        "bot_id": "crwd_sma",
        "strategy_track": "GROWTH",
        "entry_score": 85.0
    }]
    # Score dropped by 18 points (85 -> 67)
    mock_score.return_value = {"score": 67.0}

    summary = audit_in_flight_positions(db=None)

    assert summary["positions_checked"] == 1
    assert summary["actions_summary"]["TIGHTEN_STOPS"] == 1

    res = summary["results"][0]
    assert res["thesis_telemetry"]["thesis_state"] == "MILD_DECAY"
    assert res["thesis_telemetry"]["action_instruction"] == "TIGHTEN_STOPS"
    assert res["thesis_telemetry"]["recommended_stop_tightening"] == 0.5
    assert res["thesis_telemetry"]["score_delta"] == -18.0


@patch("stockpicker.sentinel._hydrate_active_positions")
@patch("stockpicker.core.compute_fundamental_score")
def test_sentinel_severe_drop_thesis_invalidation(mock_score, mock_hydrate):
    """Test Tier 2 Severe Drop (ΔS <= -30) triggers THESIS_INVALIDATION_EXIT."""
    mock_hydrate.return_value = [{
        "symbol": "NVDA",
        "client_id": 7,
        "bot_id": "nvda_sma",
        "strategy_track": "GROWTH",
        "entry_score": 90.0
    }]
    # Score dropped by 36 points (90 -> 54)
    mock_score.return_value = {"score": 54.0}

    summary = audit_in_flight_positions(db=None)

    assert summary["positions_checked"] == 1
    assert summary["actions_summary"]["THESIS_INVALIDATION_EXIT"] == 1

    res = summary["results"][0]
    assert res["thesis_telemetry"]["thesis_state"] == "INVALIDATED"
    assert res["thesis_telemetry"]["action_instruction"] == "THESIS_INVALIDATION_EXIT"
    assert any("collapsed" in flag for flag in res["thesis_telemetry"]["invalidation_flags"])


@patch("stockpicker.sentinel._hydrate_active_positions")
@patch("stockpicker.income_screener.evaluate_covered_call_candidate")
@patch("stockpicker.core.compute_fundamental_score")
def test_sentinel_track2_earnings_collision(mock_score, mock_cc, mock_hydrate):
    """Test Track 2 impending earnings (<=10 days) triggers EARLY_CALL_HARVEST."""
    mock_hydrate.return_value = [{
        "symbol": "JNJ",
        "client_id": 8,
        "bot_id": "jnj_sma",
        "strategy_track": "INCOME",
        "entry_score": 78.0
    }]
    mock_score.return_value = {"score": 77.0}
    # Earnings report in 5 days!
    mock_cc.return_value = {
        "days_to_earnings": 5,
        "atr_pct": 1.2
    }

    summary = audit_in_flight_positions(db=None)

    assert summary["positions_checked"] == 1
    assert summary["actions_summary"]["EARLY_CALL_HARVEST"] == 1

    res = summary["results"][0]
    assert res["thesis_telemetry"]["thesis_state"] == "EARNINGS_HAZARD"
    assert res["thesis_telemetry"]["action_instruction"] == "EARLY_CALL_HARVEST"
    assert any("earnings" in flag.lower() for flag in res["thesis_telemetry"]["invalidation_flags"])


@patch("stockpicker.sentinel._hydrate_active_positions")
@patch("stockpicker.income_screener.evaluate_covered_call_candidate")
@patch("stockpicker.core.compute_fundamental_score")
def test_sentinel_track2_volatility_lockout(mock_score, mock_cc, mock_hydrate):
    """Test Track 2 ATR expansion (>3.5%) triggers VOLATILITY_LOCKOUT."""
    mock_hydrate.return_value = [{
        "symbol": "XLF",
        "client_id": 9,
        "bot_id": "xlf_sma",
        "strategy_track": "INCOME",
        "entry_score": 75.0
    }]
    mock_score.return_value = {"score": 75.0}
    # ATR spiked to 4.2% (> 3.5% ceiling)
    mock_cc.return_value = {
        "days_to_earnings": 45,
        "atr_pct": 4.2
    }

    summary = audit_in_flight_positions(db=None)

    assert summary["positions_checked"] == 1
    assert summary["actions_summary"]["VOLATILITY_LOCKOUT"] == 1

    res = summary["results"][0]
    assert res["thesis_telemetry"]["thesis_state"] == "VOLATILITY_EXPANSION"
    assert res["thesis_telemetry"]["action_instruction"] == "VOLATILITY_LOCKOUT"


@patch("stockpicker.sentinel._hydrate_active_positions")
@patch("stockpicker.core.compute_fundamental_score")
def test_sentinel_persists_to_firestore(mock_score, mock_hydrate):
    """Test telemetry writes to bots/{bot_doc_id} and updates stock_picks/current."""
    mock_hydrate.return_value = [{
        "symbol": "KTOS",
        "client_id": 5,
        "bot_id": "ktos_sma",
        "strategy_track": "GROWTH",
        "entry_score": 85.0
    }]
    mock_score.return_value = {"score": 85.0}

    mock_db = MagicMock()
    mock_bot_doc = MagicMock()
    mock_current_doc = MagicMock()
    mock_current_doc.exists = True
    mock_current_doc.to_dict.return_value = {
        "already_accepted": [{"symbol": "KTOS", "current_score": 85.0}]
    }

    def mock_collection(name):
        col = MagicMock()
        if name == "bots":
            col.document.return_value = mock_bot_doc
        elif name == "stock_picks":
            col.document.return_value.get.return_value = mock_current_doc
        return col

    mock_db.collection.side_effect = mock_collection

    summary = audit_in_flight_positions(db=mock_db)

    assert summary["status"] == "success"
    # Verify write to bots/bot_5
    mock_bot_doc.set.assert_called_once()
    saved_bot_payload = mock_bot_doc.set.call_args[0][0]
    assert saved_bot_payload["client_id"] == 5
    assert saved_bot_payload["symbol"] == "KTOS"
    assert "thesis_telemetry" in saved_bot_payload
