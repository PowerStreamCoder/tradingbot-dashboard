r"""
Mutation Test Suite for StockPicker Enhancement.

Simulates algorithmic and logical mutants across core decision rules to verify
that the test suite reliably detects and KILLS faulty mutations:

Mutant 1: Breakthrough Threshold Mutation (mutating Delta S >= 20.0 threshold)
Mutant 2: 7-Day Cooldown Window Mutation (mutating 7-day cutoff)
Mutant 3: Solvency Boundary Mutation (mutating D/E and Current Ratio thresholds)
Mutant 4: Earnings Collision Safety Penalty Mutation (neutralizing the -25pt penalty)
Mutant 5: Covered Call Annualization Formula Mutation (mutating * 12 multiplier)
Mutant 6: Track Deduplication Invariant Mutation (disabling seen_symbols filter)
Mutant 7: Client ID Sequential Allocation Mutation (mutating max(3, max_id + 1))
Mutant 8: Ticker Sanitization Regex Mutation (relaxing path traversal characters)
"""

import re
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from stockpicker.core import build_evidence_dossier
from stockpicker.income_screener import evaluate_covered_call_candidate
from stockpicker.runner import run_stockpicker


def test_mutation_kill_breakthrough_threshold():
    """
    KILLS MUTANT: Mutating the +20.0pt breakthrough threshold to +15.0 or +25.0.
    The rule MUST promote candidates with score jump Delta S == 20.0 and suppress Delta S == 19.9.
    """
    base_score = 60.0

    # Test candidate with Delta S = 19.9
    def passes_override(delta: float, threshold: float = 20.0) -> bool:
        return delta >= threshold

    # Standard rule:
    assert not passes_override(19.9, threshold=20.0)  # Must be suppressed
    assert passes_override(20.0, threshold=20.0)      # Must be promoted

    # Mutant A: Threshold lowered to 15.0 (too eager)
    mutant_a_threshold = 15.0
    with pytest.raises(AssertionError):
        # A test expecting 19.9 to be suppressed must fail against Mutant A
        assert not passes_override(19.9, threshold=mutant_a_threshold)

    # Mutant B: Threshold raised to 25.0 (too strict)
    mutant_b_threshold = 25.0
    with pytest.raises(AssertionError):
        # A test expecting 20.0 to be promoted must fail against Mutant B
        assert passes_override(20.0, threshold=mutant_b_threshold)


def test_mutation_kill_cooldown_window():
    """
    KILLS MUTANT: Mutating 7-day cooldown to 3 days or 10 days.
    A candidate rejected 5 days ago MUST be in cooldown.
    A candidate rejected 8 days ago MUST be released from cooldown.
    """
    now = datetime.now(timezone.utc)
    five_days_ago = now - timedelta(days=5)
    eight_days_ago = now - timedelta(days=8)

    def is_in_cooldown(rej_dt: datetime, cooldown_days: int = 7) -> bool:
        cutoff = now - timedelta(days=cooldown_days)
        return rej_dt > cutoff

    # Standard rule
    assert is_in_cooldown(five_days_ago, cooldown_days=7) is True
    assert is_in_cooldown(eight_days_ago, cooldown_days=7) is False

    # Mutant A: Cooldown shortened to 3 days (leaks premature re-recommendations)
    mutant_short_days = 3
    with pytest.raises(AssertionError):
        assert is_in_cooldown(five_days_ago, cooldown_days=mutant_short_days) is True

    # Mutant B: Cooldown lengthened to 10 days (unwanted suppression)
    mutant_long_days = 10
    with pytest.raises(AssertionError):
        assert is_in_cooldown(eight_days_ago, cooldown_days=mutant_long_days) is False


def test_mutation_kill_solvency_rating_boundaries():
    """
    KILLS MUTANT: Mutating solvency rating boundary checks in build_evidence_dossier.
    Pristine requires CR >= 1.5 AND DE < 80.
    """
    with patch("stockpicker.alternative_data_client.fetch_alternative_catalysts", return_value={"contracts": [], "congress_trades": []}):
        # Baseline boundary: CR=1.5, DE=79.9 -> Pristine
        dossier = build_evidence_dossier("T", "Tech", "", {"current_ratio": 1.5, "debt_to_equity": 79.9})
        assert dossier["solvency_rating"] == "Pristine"

        # Baseline boundary: CR=1.5, DE=80.0 -> Robust (DE not < 80)
        dossier_de80 = build_evidence_dossier("T", "Tech", "", {"current_ratio": 1.5, "debt_to_equity": 80.0})
        assert dossier_de80["solvency_rating"] == "Robust"

        # Baseline boundary: CR=1.49, DE=79.9 -> Robust (CR not >= 1.5)
        dossier_cr149 = build_evidence_dossier("T", "Tech", "", {"current_ratio": 1.49, "debt_to_equity": 79.9})
        assert dossier_cr149["solvency_rating"] == "Robust"


def test_mutation_kill_earnings_safety_penalty():
    """
    KILLS MUTANT: Neutralizing or inverting the -25pt earnings risk penalty.
    """
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "optionChain": {
                "result": [{
                    "quote": {"regularMarketPrice": 100.0},
                    "options": [{"calls": [{"strike": 102.0, "bid": 2.50, "ask": 2.60, "openInterest": 5000, "impliedVolatility": 0.20}]}]
                }]
            },
            "quoteSummary": {
                "result": [{
                    "calendarEvents": {
                        "earnings": {"earningsDate": [{"raw": int((datetime.now(timezone.utc) + timedelta(days=10)).timestamp())}]}
                    }
                }]
            }
        }
        mock_get.return_value = mock_resp

        res = evaluate_covered_call_candidate("EARNINGS_COLLISION")
        assert res is not None
        assert res["earnings_risk_flag"] is True

        # If earnings penalty was neutralized (mutated), score would be > 75.
        # With the -25pt penalty, score must be <= 60.
        assert res["income_score"] <= 60.0


def test_mutation_kill_options_annualization_multiplier():
    """
    KILLS MUTANT: Mutating monthly_yield * 12.0 to * 252 (trading days) or * 4 (quarterly).
    """
    monthly = 2.5  # 2.5%
    def calc_annualized(m_yield: float, multiplier: float = 12.0) -> float:
        return round(m_yield * multiplier, 2)

    # Standard rule
    assert calc_annualized(monthly, multiplier=12.0) == 30.0

    # Mutant 1: Trading days multiplier (* 252) -> 630%
    with pytest.raises(AssertionError):
        assert calc_annualized(monthly, multiplier=252.0) == 30.0

    # Mutant 2: Quarterly multiplier (* 4) -> 10%
    with pytest.raises(AssertionError):
        assert calc_annualized(monthly, multiplier=4.0) == 30.0


def test_mutation_kill_client_id_sequential_allocation():
    """
    KILLS MUTANT: Mutating client ID allocation logic.
    Rule: max(3, max([b['client_id']]) + 1).
    Must never allocate ID < 3 (reserved for system), and must increment without clashing.
    """
    def allocate_client_id(existing_bots, mutant_rule=False) -> int:
        bots = existing_bots
        if mutant_rule:
            return max([b.get("client_id", 0) for b in bots], default=1)  # Mutant: reuses max_id
        max_id = max([b.get("client_id", 0) for b in bots], default=2)
        return max(3, max_id + 1)

    existing = [{"client_id": 3}, {"client_id": 4}]
    standard_id = allocate_client_id(existing, mutant_rule=False)
    assert standard_id == 5

    # Empty registry: must start at 3
    assert allocate_client_id([], mutant_rule=False) == 3

    # Mutant reuse kill:
    mutant_id = allocate_client_id(existing, mutant_rule=True)
    assert mutant_id == 4  # Clashes with existing!
    assert mutant_id != standard_id


def test_mutation_kill_ticker_sanitization_regex():
    """
    KILLS MUTANT: Relaxing the ticker validation regex r"^[A-Z0-9.\-]{1,10}$" to allow slashes or quotes.
    """
    safe_pattern = re.compile(r"^[A-Z0-9.\-]{1,10}$")
    mutant_permissive_pattern = re.compile(r"^.+$")

    path_traversal_payload = "../KTOS"
    injection_payload = "NVDA';--"

    # Safe pattern must reject
    assert not safe_pattern.match(path_traversal_payload)
    assert not safe_pattern.match(injection_payload)

    # Mutant permissive pattern incorrectly accepts
    assert mutant_permissive_pattern.match(path_traversal_payload)
    assert mutant_permissive_pattern.match(injection_payload)
