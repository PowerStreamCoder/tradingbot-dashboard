"""
Unit tests for the vendored market_hours module (dashboard #98).

The module is a verbatim copy of tradingbot-bots/trading/market_hours.py —
the bots' single source of truth for session checks. These tests pin its
behaviour in both directions (open / closed) so a divergent re-vendor is
caught here, and the dashboard's order-endpoint refusal gate
(`main._require_market_open`) stays in lockstep with the bot-side handlers
that refuse the same commands.

Dates below are chosen from the 2026 calendar: 2026-11-10 is a Tuesday,
2026-11-14 a Saturday, Thanksgiving 2026 falls on Nov 26.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from market_hours import is_market_hours, is_rth, is_us_holiday, is_eu_holiday

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Paris")


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def cet(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CET)


# ---------------------------------------------------------------------------
# is_market_hours — US default region
# ---------------------------------------------------------------------------

def test_us_weekday_within_rth_is_open():
    assert is_market_hours(et(2026, 11, 10, 10, 0)) is True


def test_us_weekend_is_closed():
    assert is_market_hours(et(2026, 11, 14, 10, 0)) is False  # Saturday
    assert is_market_hours(et(2026, 11, 15, 10, 0)) is False  # Sunday


def test_us_pre_open_is_closed():
    # 08:47 ET is the register-D16 timestamp from issue #98
    assert is_market_hours(et(2026, 11, 10, 8, 47)) is False


def test_us_post_close_is_closed():
    assert is_market_hours(et(2026, 11, 10, 16, 1)) is False


def test_us_boundaries_are_inclusive():
    assert is_market_hours(et(2026, 11, 10, 9, 30)) is True
    assert is_market_hours(et(2026, 11, 10, 16, 0)) is True


def test_us_extended_hours_widen_the_session():
    assert is_market_hours(et(2026, 11, 10, 5, 0), allow_extended_hours=True) is True
    assert is_market_hours(et(2026, 11, 10, 19, 0), allow_extended_hours=True) is True
    # Extended hours do not include the whole clock, and weekends stay closed.
    assert is_market_hours(et(2026, 11, 10, 3, 59), allow_extended_hours=True) is False
    assert is_market_hours(et(2026, 11, 14, 10, 0), allow_extended_hours=True) is False


def test_us_holidays_are_closed():
    assert is_market_hours(et(2026, 11, 26, 10, 0)) is False  # Thanksgiving
    assert is_market_hours(et(2026, 1, 19, 10, 0)) is False  # MLK Day
    assert is_market_hours(et(2026, 2, 16, 10, 0)) is False  # Presidents Day
    assert is_market_hours(et(2026, 5, 25, 10, 0)) is False  # Memorial Day
    assert is_market_hours(et(2026, 6, 19, 10, 0)) is False  # Juneteenth
    assert is_market_hours(et(2026, 9, 7, 10, 0)) is False   # Labor Day
    assert is_market_hours(et(2026, 1, 1, 10, 0)) is False  # New Year's Day
    assert is_market_hours(et(2026, 12, 25, 10, 0)) is False  # Christmas


# ---------------------------------------------------------------------------
# is_market_hours — EU region
# ---------------------------------------------------------------------------

def test_eu_weekday_within_hours_is_open():
    assert is_market_hours(cet(2026, 11, 10, 10, 0), market_region="EU") is True


def test_eu_default_hours_are_0930_1730_cet():
    assert is_market_hours(cet(2026, 11, 10, 8, 30), market_region="EU") is False
    assert is_market_hours(cet(2026, 11, 10, 18, 0), market_region="EU") is False


def test_eu_extended_hours_widen_the_session():
    assert is_market_hours(cet(2026, 11, 10, 8, 30),
                           market_region="EU", allow_extended_hours=True) is True
    assert is_market_hours(cet(2026, 11, 10, 18, 0),
                           market_region="EU", allow_extended_hours=True) is True


def test_eu_holiday_and_weekend_are_closed():
    assert is_market_hours(cet(2026, 5, 1, 10, 0), market_region="EU") is False  # Labour Day
    assert is_market_hours(cet(2026, 11, 14, 10, 0), market_region="EU") is False


# ---------------------------------------------------------------------------
# Timezone handling: aware (any zone) and naive inputs agree
# ---------------------------------------------------------------------------

def test_aware_utc_input_converted_to_et():
    # 15:00 UTC == 10:00 ET (open); 14:00 UTC == 09:00 ET (closed)
    assert is_market_hours(datetime(2026, 11, 10, 15, 0,
                                    tzinfo=ZoneInfo("UTC"))) is True
    assert is_market_hours(datetime(2026, 11, 10, 14, 0,
                                    tzinfo=ZoneInfo("UTC"))) is False


def test_naive_input_interpreted_as_local_market_time():
    assert is_market_hours(datetime(2026, 11, 10, 10, 0)) is True
    assert is_market_hours(datetime(2026, 11, 10, 8, 0)) is False


# ---------------------------------------------------------------------------
# is_rth — strict US RTH regardless of extended-hours settings
# ---------------------------------------------------------------------------

def test_is_rth_open_and_closed_directions():
    assert is_rth(et(2026, 11, 10, 10, 0)) is True
    assert is_rth(et(2026, 11, 10, 9, 30)) is True
    assert is_rth(et(2026, 11, 10, 16, 0)) is True
    assert is_rth(et(2026, 11, 10, 8, 47)) is False
    assert is_rth(et(2026, 11, 10, 16, 1)) is False
    assert is_rth(et(2026, 11, 14, 10, 0)) is False   # Saturday
    assert is_rth(et(2026, 11, 26, 10, 0)) is False   # Thanksgiving


# ---------------------------------------------------------------------------
# Holiday predicates directly
# ---------------------------------------------------------------------------

def test_is_us_holiday_fixed_and_weekday_rules():
    assert is_us_holiday(et(2026, 7, 4, 12, 0)) is True       # Independence Day
    assert is_us_holiday(et(2026, 12, 25, 12, 0)) is True    # Christmas
    # Veterans Day (Nov 11) is deliberately not in this calendar — the bots'
    # hand-rolled rules omit it, and the vendored copy must agree.
    assert is_us_holiday(et(2026, 11, 11, 12, 0)) is False
    assert is_us_holiday(et(2026, 11, 10, 12, 0)) is False


def test_is_eu_holiday_fixed_dates():
    assert is_eu_holiday(cet(2026, 1, 1, 12, 0)) is True
    assert is_eu_holiday(cet(2026, 12, 26, 12, 0)) is True
    assert is_eu_holiday(cet(2026, 7, 4, 12, 0)) is False    # not an EU holiday
