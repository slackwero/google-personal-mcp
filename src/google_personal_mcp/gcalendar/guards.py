"""Write guards for the Calendar tools: pure checks run before any write."""

from collections.abc import Sequence


def calendar_is_writable(entry: dict) -> bool:
    """This module writes only to calendars the account owns."""
    return entry.get("accessRole") == "owner"


def check_writable_calendar(entry: dict) -> None:
    if not calendar_is_writable(entry):
        role = entry.get("accessRole") or "unknown"
        raise ValueError(
            f"This calendar's access role is {role!r}; this module writes only to calendars the account owns."
        )


def edit_blockers(event: dict) -> list[str]:
    """Return one readable reason for each reason the event must not be edited or deleted."""
    reasons: list[str] = []
    event_type = event.get("eventType", "default")
    if event_type != "default":
        reasons.append(f"its type is {event_type!r}, not a regular event")
    if "attendees" in event:
        attendees = event["attendees"]
        if not isinstance(attendees, list):
            reasons.append("its attendee list is unreadable")
        elif any(not (isinstance(a, dict) and a.get("self") is True) for a in attendees):
            reasons.append("it has guests, and a change would notify them")
    if event.get("attendeesOmitted", False) is not False:
        reasons.append("its guest list is incomplete, so it may have other guests")
    organizer = event.get("organizer")
    if not isinstance(organizer, dict):
        reasons.append("its organizer is unknown, so it may be organized by someone else")
    elif organizer.get("self") is not True:
        reasons.append("it is organized by someone else")
    if event.get("status") == "cancelled":
        reasons.append("it is cancelled (already deleted)")
    if event.get("locked") is True:
        reasons.append("it is locked")
    return reasons


def check_editable(event: dict) -> None:
    reasons = edit_blockers(event)
    if reasons:
        raise ValueError("This event cannot be changed: " + "; ".join(reasons) + ".")


def check_series_flag(event: dict, *, series: bool) -> None:
    """A recurring series is only touched on purpose; `series` must match what the ID refers to."""
    is_series = bool(event.get("recurrence"))
    if is_series and not series:
        raise ValueError(
            "This is a recurring series. Pass series: true to change the whole series, "
            "or use an occurrence ID from calendar_list_events."
        )
    if series and not is_series:
        raise ValueError("series: true needs a recurring series ID; this ID is a single event or one occurrence.")


def series_ical_uid(event: dict) -> str:
    """Return the iCalUID that ties a series to its occurrences edited on their own; without it they go unchecked."""
    uid = event.get("iCalUID")
    if not isinstance(uid, str) or not uid:
        raise ValueError(
            "This series has no readable iCalUID, so its occurrences edited on their own cannot be checked; "
            "it is not changed."
        )
    return uid


def check_series_occurrences(series_id: str, items: Sequence[object]) -> None:
    """Refuse a series write when a live occurrence edited on its own (a separate event) must not be changed."""
    blocked = 0
    reasons: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            blockers = ["it could not be read"]
        elif item.get("recurringEventId") != series_id or item.get("status") == "cancelled":
            continue
        else:
            blockers = edit_blockers(item)
        if blockers:
            blocked += 1
            reasons.extend(reason for reason in blockers if reason not in reasons)
    if blocked == 1:
        which = "1 occurrence of this series was edited on its own and"
    else:
        which = f"{blocked} occurrences of this series were edited on their own and"
    if blocked:
        raise ValueError(f"This series cannot be changed: {which} cannot be changed ({'; '.join(reasons)}).")
