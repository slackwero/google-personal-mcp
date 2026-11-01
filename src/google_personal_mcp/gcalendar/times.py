"""Pure time handling for the Calendar tools: parsing user input and building Google's time bodies."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

type TimeValue = date | datetime

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?")
_FORMS_HELP = "Use one of: 2026-11-03, 2026-11-03T10:00, 2026-11-03T10:00:00-03:00"


def parse_time(text: str) -> TimeValue:
    """Parse a date, a naive datetime or an aware datetime; reject every other form."""
    try:
        if _DATE_RE.fullmatch(text):
            return date.fromisoformat(text)
        if _DATETIME_RE.fullmatch(text):
            return datetime.fromisoformat(text)
    except ValueError:
        pass
    raise ValueError(f"Invalid time {text!r}. {_FORMS_HELP}")


def zone(name: str) -> ZoneInfo:
    """Return the IANA zone called `name`, or raise a ValueError naming it."""
    try:
        if not name:
            raise ValueError("empty")
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"Unknown time zone {name!r}. Use an IANA name such as America/Santiago.") from exc


def _localize(value: datetime, tz: ZoneInfo) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=tz)


def to_rfc3339(value: TimeValue, tz: ZoneInfo) -> str:
    """Return an ISO 8601 string with an offset; a date is local midnight, a naive datetime gets `tz`."""
    if isinstance(value, datetime):
        return _localize(value, tz).isoformat()
    return datetime(value.year, value.month, value.day, tzinfo=tz).isoformat()


def event_time(value: TimeValue, time_zone: str) -> dict:
    """Build the `start` or `end` body Google expects."""
    if isinstance(value, datetime):
        return {"dateTime": value.isoformat(), "timeZone": time_zone}
    return {"date": value.isoformat()}


def check_span(start: TimeValue, end: TimeValue, tz: ZoneInfo) -> None:
    """Reject mixing an all-day value with a timed one, and an end that is not after the start."""
    if isinstance(start, datetime) != isinstance(end, datetime):
        raise ValueError("start and end must both be all-day dates or both be date-times; do not mix them.")
    if isinstance(start, datetime) and isinstance(end, datetime):
        start, end = _localize(start, tz), _localize(end, tz)
    if end <= start:
        raise ValueError("The end must be after the start.")


def all_day_default_end(start: date) -> date:
    """An all-day event with no end lasts one day (Google's end date is exclusive)."""
    return start + timedelta(days=1)


def parse_google_time(body: object, tz: ZoneInfo) -> TimeValue:
    """Read an event's `start` or `end` as Google returns it; anything unreadable is a ValueError."""
    try:
        if isinstance(body, dict) and "dateTime" in body:
            return _localize(datetime.fromisoformat(body["dateTime"]), tz)
        if isinstance(body, dict):
            return date.fromisoformat(body["date"])
    except KeyError, TypeError, ValueError:
        pass
    raise ValueError("The event's start or end could not be read; pass both start and end.")


def shift_end(old_start: dict, old_end: dict, new_start: TimeValue, tz: ZoneInfo) -> TimeValue:
    """Return the end that keeps the event's old duration when it moves to `new_start`."""
    old_s = parse_google_time(old_start, tz)
    old_e = parse_google_time(old_end, tz)
    if isinstance(old_s, datetime) != isinstance(new_start, datetime):
        raise ValueError("The new start is of a different kind (all-day or timed) than the event; pass an end too.")
    return new_start + (old_e - old_s)
