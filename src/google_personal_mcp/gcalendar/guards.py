"""Write guards for the Calendar tools: pure checks run before any write."""


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
