"""Pure follow-up timing: anchor + rule offset, then shifted into business hours (tenant timezone)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .bundle import WEEKDAYS, BusinessHours, FollowupRule
from .dates import VisitWhen


def into_business_hours(when: datetime, hours: BusinessHours, tz: str) -> datetime:
    """Returns `when` if inside business hours, otherwise the next opening time (UTC)."""
    z = ZoneInfo(tz)
    local = when.astimezone(z)
    for _ in range(8):
        day_ok = WEEKDAYS[local.weekday()] in hours.days
        open_dt = local.replace(hour=hours.open_time.hour, minute=hours.open_time.minute, second=0, microsecond=0)
        close_dt = local.replace(hour=hours.close_time.hour, minute=hours.close_time.minute, second=0, microsecond=0)
        if day_ok and open_dt <= local < close_dt:
            return local.astimezone(timezone.utc)
        if day_ok and local < open_dt:
            return open_dt.astimezone(timezone.utc)
        nxt = (local + timedelta(days=1)).replace(hour=hours.open_time.hour, minute=hours.open_time.minute, second=0, microsecond=0)
        local = nxt
    return when


def is_business_time(when: datetime, hours: BusinessHours, tz: str) -> bool:
    return into_business_hours(when, hours, tz) == when.astimezone(timezone.utc)


def visit_window_utc(visit_date: date, time_window: Optional[str], precision: str, hours: BusinessHours, tz: str) -> tuple[datetime, datetime]:
    vw = VisitWhen(visit_date, precision, time_window)  # type: ignore[arg-type]
    z = ZoneInfo(tz)
    start = datetime.combine(vw.date, vw.start_time(hours.open_time), tzinfo=z)
    end = datetime.combine(vw.last_day(), vw.end_time(hours.close_time), tzinfo=z)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def due_for_rule(rule: FollowupRule, anchor: datetime, hours: BusinessHours, tz: str) -> datetime:
    due = anchor + timedelta(minutes=rule.offset_minutes)
    if rule.before:
        # reminders must land before the visit: if outside hours, pull back to the previous close
        z = ZoneInfo(tz)
        local = due.astimezone(z)
        if not is_business_time(due, hours, tz):
            for _ in range(8):
                close_dt = local.replace(hour=hours.close_time.hour, minute=hours.close_time.minute, second=0, microsecond=0) - timedelta(minutes=30)
                if WEEKDAYS[local.weekday()] in hours.days and close_dt <= local:
                    return close_dt.astimezone(timezone.utc)
                local = (local - timedelta(days=1)).replace(hour=23, minute=59)
        return due
    return into_business_hours(due, hours, tz)
