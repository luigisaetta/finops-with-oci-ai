"""
Agent utils
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def month_bounds(year: int, month: int, tz: str = "Europe/Rome"):
    """
    Compute start/end dates, today, days observed, remaining days for a given month/year in tz.
    """
    z = ZoneInfo(tz)
    start = datetime(year, month, 1, tzinfo=z)
    # compute first day of next month then step back 1 day
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=z)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=z)
    end = next_month - timedelta(days=1)
    # "today" in target timezone (used for soft/hard check logic)
    now_tz = datetime.now(z)
    # clamp "analysis_today" inside the month window for reproducible demos
    if now_tz < start:
        analysis_today = start
    elif now_tz > end:
        analysis_today = end
    else:
        analysis_today = now_tz
    days_observed = (analysis_today.date() - start.date()).days + 1
    remaining_days = (end.date() - analysis_today.date()).days
    is_month_end = analysis_today.date() == end.date()
    return {
        "tz": tz,
        "start": start,
        "end": end,
        "today": analysis_today,
        "days_observed": max(0, days_observed),
        "remaining_days": max(0, remaining_days),
        "is_month_end": is_month_end,
    }