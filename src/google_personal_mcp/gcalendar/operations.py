"""Calendar write operations: create, update and delete one event, behind the write guards.

The operations take a `before_write` hook that runs immediately before the write and may raise to stop it; the tools
pass `anyio.from_thread.check_cancelled`, so a cancelled tool call starts no new write. After each successful write one
INFO line with the tool name and the event ID goes to the log (stderr), never titles, descriptions, times or calendar
IDs, so a change can be undone even when the tool response is lost.
"""

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from google_personal_mcp.gcalendar.client import CalendarClient
from google_personal_mcp.gcalendar.events import EventDetail, parse_event
from google_personal_mcp.gcalendar.guards import (
    calendar_is_writable,
    check_editable,
    check_series_flag,
    check_series_occurrences,
    check_writable_calendar,
    edit_blockers,
    series_ical_uid,
)
from google_personal_mcp.gcalendar.times import (
    TimeValue,
    all_day_default_end,
    check_span,
    event_time,
    parse_google_time,
    shift_end,
    zone,
)

logger = logging.getLogger(__name__)

DEFAULT_ZONE = "UTC"
MAX_RECURRENCE_LINES = 10
RECURRENCE_PREFIXES = ("RRULE:", "EXRULE:", "RDATE:", "EXDATE:")
SNAPSHOT_DESCRIPTION_CHARS = 100_000


@dataclass(frozen=True)
class EventInput:
    """The event fields a write may set; `None` means "not given"."""

    summary: str | None = None
    start: TimeValue | None = None
    end: TimeValue | None = None
    time_zone: str | None = None
    description: str | None = None
    location: str | None = None
    recurrence: list[str] | None = None


class UpdateResult(BaseModel):
    """What calendar_update_event changed. `before` is keyed by the tool's parameter names, so it can be passed back."""

    before: dict[str, Any]
    event: EventDetail


def _allow_write() -> None:
    """The default `before_write` hook: nothing stops the write."""


def _log_write(tool: str, event_id: str) -> None:
    """Log one successful write with the event ID only; JSON encoding keeps the record on one line."""
    logger.info("%s wrote event: id=%s", tool, json.dumps(event_id))


def validate_recurrence(lines: Sequence[str]) -> list[str]:
    """Accept only a short list of single-line RFC 5545 recurrence properties."""
    if len(lines) > MAX_RECURRENCE_LINES:
        raise ValueError(f"recurrence takes at most {MAX_RECURRENCE_LINES} lines.")
    for line in lines:
        if "\n" in line or "\r" in line:
            raise ValueError("Each recurrence line must be a single line.")
        if not line.startswith(RECURRENCE_PREFIXES):
            raise ValueError(f"Each recurrence line must start with one of {', '.join(RECURRENCE_PREFIXES)}")
    return list(lines)


def _depends_on_zone(first: TimeValue, second: TimeValue) -> bool:
    """True when comparing the two needs a time zone: one naive and one aware datetime."""
    return (
        isinstance(first, datetime)
        and isinstance(second, datetime)
        and (first.tzinfo is None) != (second.tzinfo is None)
    )


def check_span_if_zone_free(start: TimeValue, end: TimeValue) -> None:
    """Run `check_span` now when the answer cannot depend on the calendar's zone (any zone gives the same answer)."""
    if not _depends_on_zone(start, end):
        check_span(start, end, zone(DEFAULT_ZONE))


def _check_zone_and_recurrence(fields: EventInput) -> None:
    if fields.time_zone is not None:
        zone(fields.time_zone)
    if fields.recurrence is not None:
        validate_recurrence(fields.recurrence)


def _resolve_end(start: TimeValue, end: TimeValue | None) -> TimeValue:
    """The given end, or the all-day default; a timed event has no default."""
    if end is not None:
        return end
    if isinstance(start, datetime):
        raise ValueError("end is required for events with a time; only all-day events default to one day.")
    return all_day_default_end(start)


def precheck_create(fields: EventInput) -> tuple[str, TimeValue, TimeValue]:
    """Every check for a create that needs no client call; returns the summary, start and resolved end."""
    if not fields.summary:
        raise ValueError("summary is required.")
    if fields.start is None:
        raise ValueError("start is required.")
    _check_zone_and_recurrence(fields)
    end = _resolve_end(fields.start, fields.end)
    check_span_if_zone_free(fields.start, end)
    return fields.summary, fields.start, end


def precheck_update(fields: EventInput) -> None:
    """Every check for an update that needs no client call."""
    given = [value for value in vars(fields).values() if value is not None]
    if not given:
        raise ValueError("Give at least one field to change.")
    if fields.time_zone is not None and fields.start is None and fields.end is None:
        raise ValueError("time_zone applies to start and end; give start or end with it.")
    _check_zone_and_recurrence(fields)
    if fields.start is not None and fields.end is not None:
        check_span_if_zone_free(fields.start, fields.end)


def _entry_zone_name(entry: dict) -> str:
    name = entry.get("timeZone")
    return name if isinstance(name, str) and name else DEFAULT_ZONE


def _calendar_id(entry: dict, calendar_id: str) -> str:
    resolved = entry.get("id")
    return resolved if isinstance(resolved, str) and resolved else calendar_id


def _detail(item: dict, entry: dict, calendar_id: str, *, editable: bool, max_chars: int = 8_192) -> EventDetail:
    return parse_event(
        item,
        calendar_id=_calendar_id(entry, calendar_id),
        calendar_time_zone=_entry_zone_name(entry),
        editable=editable,
        max_description_chars=max_chars,
    )


def create_event(
    client: CalendarClient,
    calendar_id: str,
    fields: EventInput,
    *,
    before_write: Callable[[], None] = _allow_write,
) -> EventDetail:
    """Create an event with no guests on a calendar the account owns."""
    summary, start, end = precheck_create(fields)
    entry = client.get_calendar_entry(calendar_id)
    check_writable_calendar(entry)
    tz_name = fields.time_zone or _entry_zone_name(entry)
    check_span(start, end, zone(tz_name))
    body: dict[str, Any] = {
        "summary": summary,
        "start": event_time(start, tz_name),
        "end": event_time(end, tz_name),
    }
    for key in ("description", "location", "recurrence"):
        value = getattr(fields, key)
        if value is not None:
            body[key] = value
    before_write()
    created = client.insert_event(calendar_id, body)
    _log_write("calendar_create_event", _event_id(created))
    return _detail(created, entry, calendar_id, editable=_editable(entry, created))


def _event_id(item: dict) -> str:
    event_id = item.get("id")
    return event_id if isinstance(event_id, str) else ""


def _editable(entry: dict, item: dict) -> bool:
    return calendar_is_writable(entry) and not edit_blockers(item)


def _guarded_event(client: CalendarClient, calendar_id: str, event_id: str, *, series: bool) -> tuple[dict, dict]:
    entry = client.get_calendar_entry(calendar_id)
    check_writable_calendar(entry)
    event = client.get_event(calendar_id, event_id)
    check_editable(event)
    check_series_flag(event, series=series)
    if series:
        # An occurrence edited on its own is a separate event that can have its own guests.
        exceptions = client.list_series_exceptions(calendar_id, series_ical_uid(event))
        check_series_occurrences(_event_id(event) or event_id, exceptions)
    return entry, event


def _old_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _time_string(body: object) -> str:
    if isinstance(body, dict):
        for key in ("dateTime", "date"):
            if isinstance(body.get(key), str):
                return body[key]
    return ""


def _patch_time(value: TimeValue, tz_name: str, old: object) -> dict[str, Any]:
    """The `start` or `end` patch; switching between all-day and timed nulls the keys of the old kind."""
    patch: dict[str, Any] = event_time(value, tz_name)
    old_body = old if isinstance(old, dict) else {}
    old_timed = "dateTime" in old_body
    new_timed = isinstance(value, datetime)
    if old_timed != new_timed:
        for key in ("date", "dateTime", "timeZone"):
            patch.setdefault(key, None)
    return patch


def update_event(
    client: CalendarClient,
    calendar_id: str,
    event_id: str,
    changes: EventInput,
    *,
    series: bool,
    before_write: Callable[[], None] = _allow_write,
) -> UpdateResult:
    """Patch only the given fields of an event and report the values they replaced."""
    precheck_update(changes)
    entry, event = _guarded_event(client, calendar_id, event_id, series=series)
    if changes.recurrence is not None and event.get("recurringEventId"):
        raise ValueError("recurrence can only be set on a series or a non-recurring event, not on an occurrence.")
    old_start = event.get("start")
    old_end = event.get("end")
    old_zone = old_start.get("timeZone") if isinstance(old_start, dict) else None
    tz_name = changes.time_zone or (old_zone if isinstance(old_zone, str) and old_zone else _entry_zone_name(entry))
    tz = zone(tz_name)

    new_start = changes.start
    new_end = changes.end
    if new_start is not None and new_end is None:
        new_end = shift_end(old_start, old_end, new_start, tz)  # type: ignore[arg-type]
    elif new_end is not None and new_start is None:
        check_span(parse_google_time(old_start, tz), new_end, tz)
    if new_start is not None and new_end is not None:
        check_span(new_start, new_end, tz)

    body: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if changes.summary is not None:
        body["summary"] = changes.summary
        before["summary"] = _old_text(event.get("summary"))
    if new_start is not None:
        body["start"] = _patch_time(new_start, tz_name, old_start)
        before["start"] = _time_string(old_start)
    if new_end is not None:
        body["end"] = _patch_time(new_end, tz_name, old_end)
        before["end"] = _time_string(old_end)
    if (new_start is not None or new_end is not None) and isinstance(old_zone, str) and old_zone:
        before["time_zone"] = old_zone
    for key in ("description", "location"):
        value = getattr(changes, key)
        if value is not None:
            body[key] = value
            before[key] = _old_text(event.get(key))
    if changes.recurrence is not None:
        body["recurrence"] = changes.recurrence
        old_recurrence = event.get("recurrence")
        before["recurrence"] = (
            [line for line in old_recurrence if isinstance(line, str)] if isinstance(old_recurrence, list) else []
        )

    before_write()
    patched = client.patch_event(calendar_id, event_id, body)
    _log_write("calendar_update_event", _event_id(patched) or event_id)
    return UpdateResult(before=before, event=_detail(patched, entry, calendar_id, editable=_editable(entry, patched)))


def delete_event(
    client: CalendarClient,
    calendar_id: str,
    event_id: str,
    *,
    series: bool,
    before_write: Callable[[], None] = _allow_write,
) -> EventDetail:
    """Delete an event and return its snapshot, complete enough to re-create it."""
    entry, event = _guarded_event(client, calendar_id, event_id, series=series)
    snapshot = _detail(event, entry, calendar_id, editable=False, max_chars=SNAPSHOT_DESCRIPTION_CHARS)
    before_write()
    client.delete_event(calendar_id, event_id)
    _log_write("calendar_delete_event", _event_id(event) or event_id)
    return snapshot
