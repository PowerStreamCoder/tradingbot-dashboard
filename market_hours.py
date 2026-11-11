"""
Market hours utilities — single source of truth for trading session checks.

Used by both base.py (TradingBot) and execution_manager.py (ExecutionManager).

VENDORED COPY (dashboard #98): copied verbatim from
tradingbot-bots/trading/market_hours.py so the dashboard's order endpoints
(/api/create-position, /api/close-position) refuse commands using the SAME
session rules the bots' handlers use to refuse them (region- and
extended-hours-aware, holiday calendar included) — the two sides cannot
disagree about what "market is closed" means.

Do not change the logic here in isolation: change it in the bots repo first,
then re-vendor this copy. tests/test_market_hours.py pins the expected
behaviour so a divergent copy is caught in CI.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Paris")

# Fixed US holidays as (month, day)
_US_HOLIDAYS_FIXED = {
    (1, 1),   # New Year's Day
    (1, 2),   # New Year's Day observed (when Jan 1 is Sunday)
    (7, 3),   # Independence Day observed (when Jul 4 is Saturday)
    (7, 4),   # Independence Day
    (7, 5),   # Independence Day observed (when Jul 4 is Sunday)
    (12, 24), # Christmas Eve observed (when Dec 25 is Saturday)
    (12, 25), # Christmas Day
    (12, 26), # Christmas Day observed (when Dec 25 is Sunday)
}

# Fixed EU holidays as (month, day)
_EU_HOLIDAYS_FIXED = {
    (1, 1),   # New Year's Day
    (5, 1),   # Labour Day
    (12, 25), # Christmas Day
    (12, 26), # Boxing Day / St. Stephen's Day
}


def is_us_holiday(ts: datetime) -> bool:
    """Return True if ts falls on a US stock market holiday."""
    md = (ts.month, ts.day)
    if md in _US_HOLIDAYS_FIXED:
        return True
    # MLK Day: 3rd Monday of January
    if ts.month == 1 and ts.weekday() == 0 and 15 <= ts.day <= 21:
        return True
    # Presidents Day: 3rd Monday of February
    if ts.month == 2 and ts.weekday() == 0 and 15 <= ts.day <= 21:
        return True
    # Memorial Day: Last Monday of May
    if ts.month == 5 and ts.weekday() == 0 and ts.day >= 25:
        return True
    # Juneteenth: June 19 (observed June 18 if Friday, June 20 if Monday)
    if ts.month == 6 and ts.day in (18, 19, 20):
        if ts.day == 19 or (ts.day == 18 and ts.weekday() == 4) or (ts.day == 20 and ts.weekday() == 0):
            return True
    # Labor Day: 1st Monday of September
    if ts.month == 9 and ts.weekday() == 0 and ts.day <= 7:
        return True
    # Thanksgiving: 4th Thursday of November
    if ts.month == 11 and ts.weekday() == 3 and 22 <= ts.day <= 28:
        return True
    return False


def is_eu_holiday(ts: datetime) -> bool:
    """Return True if ts falls on an EU stock market holiday."""
    return (ts.month, ts.day) in _EU_HOLIDAYS_FIXED


def is_market_hours(ts: datetime, market_region: str = "US",
                    allow_extended_hours: bool = False) -> bool:
    """
    Return True if ts falls within trading hours for the given region.
    Excludes weekends and holidays.
    """
    if ts.weekday() >= 5:
        return False

    if market_region == "EU":
        ts_local = ts.astimezone(CET) if ts.tzinfo else ts.replace(tzinfo=CET)
        if is_eu_holiday(ts_local):
            return False
        open_ = time(8, 0) if allow_extended_hours else time(9, 0)
        close = time(18, 30) if allow_extended_hours else time(17, 30)
    else:
        ts_local = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=ET)
        if is_us_holiday(ts_local):
            return False
        open_ = time(4, 0) if allow_extended_hours else time(9, 30)
        close = time(20, 0) if allow_extended_hours else time(16, 0)

    return open_ <= ts_local.time() <= close


def is_rth(ts: datetime) -> bool:
    """
    Return True if ts is within strict US Regular Trading Hours (9:30–16:00 ET),
    excluding weekends and US holidays.

    Used to gate features like IBKR Adaptive algo that are only allowed during RTH
    regardless of extended-hours settings.
    """
    if ts.weekday() >= 5:
        return False
    ts_et = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=ET)
    if is_us_holiday(ts_et):
        return False
    return time(9, 30) <= ts_et.time() <= time(16, 0)
