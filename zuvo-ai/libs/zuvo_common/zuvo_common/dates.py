"""Deterministic visit-date resolution for English / Hindi / Hinglish expressions.

`resolve_visit("Saturday ko aayenge shaam ko", today)` ->
    VisitWhen(date=<next Saturday>, precision="WEEKDAY", time_window="evening")
The LLM also proposes a date; this resolver wins whenever it recognises the expression
because it is reproducible and timezone-correct.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

Precision = Literal["EXACT", "WEEKDAY", "WEEK", "MONTH"]

_WEEKDAY_WORDS = {
    0: ["monday", "mon", "somvar", "somwar", "som"],
    1: ["tuesday", "tue", "tues", "mangalvar", "mangalwar", "mangal"],
    2: ["wednesday", "wed", "budhvar", "budhwar", "budh"],
    3: ["thursday", "thu", "thurs", "guruvar", "guruwar", "brihaspativar", "veerwar", "guru"],
    4: ["friday", "fri", "shukravar", "shukrawar", "shukra"],
    5: ["saturday", "sat", "shanivar", "shaniwar", "shani"],
    6: ["sunday", "sun", "ravivar", "raviwar", "itwar", "itvaar", "ravi"],
}
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WINDOWS = {
    "morning": ["morning", "subah", "subha", "savere"],
    "afternoon": ["afternoon", "dopahar", "dopher", "lunch"],
    "evening": ["evening", "shaam", "sham", "sam"],
}
WINDOW_START = {"morning": time(10, 0), "afternoon": time(14, 0), "evening": time(17, 0)}
WINDOW_END = {"morning": time(13, 0), "afternoon": time(17, 0), "evening": time(20, 0)}


@dataclass(frozen=True)
class VisitWhen:
    date: date
    precision: Precision
    time_window: Optional[str] = None  # morning|afternoon|evening|HH:MM
    matched: str = ""

    def start_time(self, default_open: time) -> time:
        if self.time_window in WINDOW_START:
            return WINDOW_START[self.time_window]
        if self.time_window and re.fullmatch(r"\d{2}:\d{2}", self.time_window):
            return time.fromisoformat(self.time_window)
        return default_open

    def end_time(self, default_close: time) -> time:
        if self.time_window in WINDOW_END:
            return WINDOW_END[self.time_window]
        if self.time_window and re.fullmatch(r"\d{2}:\d{2}", self.time_window):
            t = time.fromisoformat(self.time_window)
            return time(min(t.hour + 2, 23), t.minute)
        return default_close

    def last_day(self) -> date:
        if self.precision == "WEEK":
            return self.date + timedelta(days=6)
        if self.precision == "MONTH":
            nxt = (self.date.replace(day=28) + timedelta(days=4)).replace(day=1)
            return nxt - timedelta(days=1)
        return self.date

    def describe(self) -> str:
        if self.precision == "WEEK":
            label = f"the week of {self.date.strftime('%d %b')}"
        elif self.precision == "MONTH":
            label = self.date.strftime("%B %Y")
        else:
            label = self.date.strftime("%A, %d %b")
        if self.time_window in WINDOW_START:
            label += f" ({self.time_window})"
        elif self.time_window:
            label += f" at {self.time_window}"
        return label


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+", text.lower())


def _time_window(text: str) -> Optional[str]:
    low = text.lower()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bje)\b", low)
    if m:
        h, mi, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if suffix == "pm" and h < 12:
            h += 12
        elif suffix in ("baje", "bje") and 1 <= h <= 7:
            h += 12  # "5 baje" at a shop means 5 PM
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    words = set(_words(low))
    for window, keys in _WINDOWS.items():
        if words & set(keys):
            return window
    return None


def _next_weekday(today: date, wd: int, force_next_week: bool) -> date:
    delta = (wd - today.weekday()) % 7
    if force_next_week:
        delta = delta + 7 if delta != 0 else 7
        # "next saturday" said on Monday usually means this coming saturday + 7 only if the
        # speaker explicitly says "agle hafte"; keep simple: next week's occurrence
    return today + timedelta(days=delta)


def resolve_visit(text: str, today: date) -> Optional[VisitWhen]:
    if not text:
        return None
    low = text.lower()
    words = _words(low)
    wset = set(words)
    tw = _time_window(low)
    next_week = bool(re.search(r"\b(next week|agle hafte|agle week|next hafte|agla hafta)\b", low))

    # ISO / numeric dates: 2026-06-15, 15/6, 15-06-2026
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", low)
    if m:
        try:
            return VisitWhen(date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "EXACT", tw, m.group(0))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", low)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else today.year
        yr = yr + 2000 if yr < 100 else yr
        try:
            cand = date(yr, mo, d)
            if not m.group(3) and cand < today:
                cand = date(yr + 1, mo, d)
            return VisitWhen(cand, "EXACT", tw, m.group(0))
        except ValueError:
            pass
    # "15 june", "june 15", "15th jun"
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\b", low) or None
    m2 = re.search(r"\b([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
    for mm, d_idx, m_idx in ((m, 1, 2), (m2, 2, 1)):
        if mm and mm.group(m_idx)[:3] in _MONTHS:
            try:
                cand = date(today.year, _MONTHS[mm.group(m_idx)[:3]], int(mm.group(d_idx)))
                if cand < today:
                    cand = cand.replace(year=today.year + 1)
                return VisitWhen(cand, "EXACT", tw, mm.group(0))
            except ValueError:
                pass
    # day of month only: "20th", "20 tarikh" -> this month, or next month if already past
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", low) or re.search(r"\b(\d{1,2})\s*(?:tarikh|tareekh|tarik|taarikh|date)\b", low)
    if m and 1 <= int(m.group(1)) <= 31:
        y, mo = today.year, today.month
        for _ in range(3):
            try:
                cand = date(y, mo, int(m.group(1)))
                if cand >= today:
                    return VisitWhen(cand, "EXACT", tw, m.group(0))
            except ValueError:
                pass
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)

    if wset & {"today", "aaj", "aj", "abhi"}:
        return VisitWhen(today, "EXACT", tw, "today")
    if "parso" in wset or "parson" in wset or "day after tomorrow" in low:
        return VisitWhen(today + timedelta(days=2), "EXACT", tw, "day after tomorrow")
    if wset & {"tomorrow", "kal", "tmrw", "tmr", "kl"}:
        # "kal" is ambiguous (yesterday/tomorrow) but in a visit context it means tomorrow
        return VisitWhen(today + timedelta(days=1), "EXACT", tw, "tomorrow")

    for wd, keys in _WEEKDAY_WORDS.items():
        # 3-letter abbreviations only count as whole words ("sat"), and short hindi stems
        # ("som", "ravi", "shani") only when followed by "ko"/"vaar" to avoid false hits.
        for k in keys:
            if k in wset and (len(k) > 4 or k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") or re.search(rf"\b{k}\s+(ko|vaar|var|war)\b", low)):
                return VisitWhen(_next_weekday(today, wd, next_week), "WEEKDAY", tw, k)
    if "weekend" in wset:
        return VisitWhen(_next_weekday(today, 5, next_week), "WEEKDAY", tw, "weekend")
    if next_week:
        monday = today + timedelta(days=(7 - today.weekday()))
        return VisitWhen(monday, "WEEK", tw, "next week")
    if re.search(r"\b(this week|is hafte|isi hafte|is week)\b", low):
        return VisitWhen(today, "WEEK", tw, "this week")
    if re.search(r"\b(next month|agle mahine|agle month)\b", low):
        first = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return VisitWhen(first, "MONTH", tw, "next month")
    return None


def parse_iso_date(value: Optional[str], today: date, max_days: int = 180) -> Optional[date]:
    if not value:
        return None
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return d if today <= d <= today + timedelta(days=max_days) else None


def local_today(tz: str, now: Optional[datetime] = None) -> date:
    from zoneinfo import ZoneInfo

    return (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(tz)).date()
