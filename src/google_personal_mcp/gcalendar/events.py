"""Pure parsing of Calendar API event dicts into models. Event content is untrusted: nothing here raises."""

from pydantic import BaseModel


class Attendee(BaseModel):
    email: str
    display_name: str
    response_status: str
    organizer: bool
    is_self: bool
    optional: bool


class EventSummary(BaseModel):
    """List-level view of an event."""

    id: str
    calendar_id: str
    summary: str
    location: str
    start: str
    end: str
    all_day: bool
    time_zone: str
    status: str
    event_type: str = "default"
    recurring_event_id: str | None
    organizer_self: bool
    other_attendees: int
    editable: bool


class EventDetail(EventSummary):
    """Full view of one event."""

    description: str
    description_truncated: bool
    attendees: list[Attendee]
    organizer_email: str
    organizer_name: str
    creator_email: str
    recurrence: list[str]
    original_start: str | None
    conference_link: str | None
    html_link: str | None


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _time_value(body: object) -> str:
    fields = _dict(body)
    return _text(fields.get("dateTime")) or _text(fields.get("date"))


def _attendee_dicts(item: dict) -> list[dict]:
    attendees = item.get("attendees")
    return [a for a in attendees if isinstance(a, dict)] if isinstance(attendees, list) else []


def _summary_fields(item: dict, *, calendar_id: str, calendar_time_zone: str, editable: bool) -> dict:
    start = _dict(item.get("start"))
    recurring = item.get("recurringEventId")
    return {
        "id": _text(item.get("id")),
        "calendar_id": calendar_id,
        "summary": _text(item.get("summary")),
        "location": _text(item.get("location")),
        "start": _time_value(start),
        "end": _time_value(item.get("end")),
        "all_day": "date" in start and "dateTime" not in start,
        "time_zone": _text(start.get("timeZone")) or calendar_time_zone,
        "status": _text(item.get("status")),
        "event_type": _text(item.get("eventType")) or "default",
        "recurring_event_id": recurring if isinstance(recurring, str) and recurring else None,
        "organizer_self": _dict(item.get("organizer")).get("self") is True,
        "other_attendees": sum(1 for a in _attendee_dicts(item) if a.get("self") is not True),
        "editable": editable,
    }


def summarize_event(item: dict, *, calendar_id: str, calendar_time_zone: str, editable: bool) -> EventSummary:
    return EventSummary(
        **_summary_fields(item, calendar_id=calendar_id, calendar_time_zone=calendar_time_zone, editable=editable)
    )


def _attendee(raw: dict) -> Attendee:
    return Attendee(
        email=_text(raw.get("email")),
        display_name=_text(raw.get("displayName")),
        response_status=_text(raw.get("responseStatus")),
        organizer=raw.get("organizer") is True,
        is_self=raw.get("self") is True,
        optional=raw.get("optional") is True,
    )


def _conference_link(item: dict) -> str | None:
    hangout = _text(item.get("hangoutLink"))
    if hangout:
        return hangout
    entry_points = _dict(item.get("conferenceData")).get("entryPoints")
    for entry in entry_points if isinstance(entry_points, list) else []:
        entry = _dict(entry)
        if entry.get("entryPointType") == "video" and _text(entry.get("uri")):
            return entry["uri"]
    return None


def parse_event(
    item: dict, *, calendar_id: str, calendar_time_zone: str, editable: bool, max_description_chars: int
) -> EventDetail:
    description = _text(item.get("description"))
    organizer = _dict(item.get("organizer"))
    recurrence = item.get("recurrence")
    return EventDetail(
        **_summary_fields(item, calendar_id=calendar_id, calendar_time_zone=calendar_time_zone, editable=editable),
        description=description[:max_description_chars],
        description_truncated=len(description) > max_description_chars,
        attendees=[_attendee(a) for a in _attendee_dicts(item)],
        organizer_email=_text(organizer.get("email")),
        organizer_name=_text(organizer.get("displayName")),
        creator_email=_text(_dict(item.get("creator")).get("email")),
        recurrence=[r for r in recurrence if isinstance(r, str)] if isinstance(recurrence, list) else [],
        original_start=_time_value(item.get("originalStartTime")) or None,
        conference_link=_conference_link(item),
        html_link=_text(item.get("htmlLink")) or None,
    )
