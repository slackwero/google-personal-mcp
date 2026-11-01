import pytest

from google_personal_mcp.gcalendar.events import parse_event, summarize_event

CAL = "primary"
CAL_TZ = "America/Santiago"


def summarize(item, **kw):
    return summarize_event(item, calendar_id=CAL, calendar_time_zone=CAL_TZ, editable=kw.pop("editable", True))


def test_timed_and_all_day_summaries():
    timed = summarize(
        {
            "id": "e1",
            "summary": "Standup",
            "location": "Room 1",
            "status": "confirmed",
            "start": {"dateTime": "2026-11-03T10:00:00-03:00", "timeZone": "Europe/Madrid"},
            "end": {"dateTime": "2026-11-03T10:30:00-03:00"},
            "organizer": {"self": True},
        }
    )
    assert (timed.id, timed.calendar_id, timed.summary, timed.location) == ("e1", CAL, "Standup", "Room 1")
    assert timed.start == "2026-11-03T10:00:00-03:00"
    assert timed.end == "2026-11-03T10:30:00-03:00"
    assert timed.all_day is False
    assert timed.time_zone == "Europe/Madrid"
    assert timed.organizer_self is True
    assert timed.event_type == "default"
    assert timed.editable is True

    all_day = summarize({"id": "e2", "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-04"}}, editable=False)
    assert all_day.all_day is True
    assert (all_day.start, all_day.end) == ("2026-11-03", "2026-11-04")
    assert all_day.time_zone == CAL_TZ
    assert all_day.editable is False
    assert all_day.organizer_self is False


def test_other_attendees_excludes_self():
    item = {"id": "e", "attendees": [{"email": "me@example.com", "self": True}, {"email": "a@example.com"}]}
    assert summarize(item).other_attendees == 1


def test_recurring_event_id_is_kept():
    assert summarize({"id": "s_20261103T130000Z", "recurringEventId": "s"}).recurring_event_id == "s"


def test_detail_truncates_description_and_finds_the_video_link():
    item = {
        "id": "e",
        "description": "x" * 50,
        "htmlLink": "https://calendar.example.com/e",
        "organizer": {"email": "o@example.com", "displayName": "Org"},
        "creator": {"email": "c@example.com"},
        "recurrence": ["RRULE:FREQ=WEEKLY"],
        "originalStartTime": {"dateTime": "2026-11-03T10:00:00-03:00"},
        "attendees": [
            {"email": "a@example.com", "displayName": "A", "responseStatus": "accepted", "optional": True},
            {"email": "me@example.com", "self": True, "organizer": True},
        ],
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "phone", "uri": "tel:1"},
                {"entryPointType": "video", "uri": "https://meet.example.com/abc"},
            ]
        },
    }
    d = parse_event(item, calendar_id=CAL, calendar_time_zone=CAL_TZ, editable=True, max_description_chars=10)
    assert d.description == "x" * 10
    assert d.description_truncated is True
    assert d.conference_link == "https://meet.example.com/abc"
    assert d.html_link == "https://calendar.example.com/e"
    assert (d.organizer_email, d.organizer_name, d.creator_email) == ("o@example.com", "Org", "c@example.com")
    assert d.recurrence == ["RRULE:FREQ=WEEKLY"]
    assert d.original_start == "2026-11-03T10:00:00-03:00"
    assert d.attendees[0].display_name == "A"
    assert d.attendees[0].response_status == "accepted"
    assert d.attendees[0].optional is True
    assert d.attendees[1].is_self is True and d.attendees[1].organizer is True

    short = parse_event(
        {"id": "e", "description": "abc", "hangoutLink": "https://meet.example.com/h"},
        calendar_id=CAL,
        calendar_time_zone=CAL_TZ,
        editable=True,
        max_description_chars=10,
    )
    assert short.description == "abc" and short.description_truncated is False
    assert short.conference_link == "https://meet.example.com/h"
    assert short.original_start is None and short.html_link is None


@pytest.mark.parametrize(
    "item",
    [
        {"id": "x"},
        {"id": "x", "start": {}, "end": {}, "eventType": "birthday"},
        {"id": "x", "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-04"}},
        {},
        {"id": "x", "start": None, "attendees": None, "organizer": "oops", "conferenceData": {"entryPoints": [None]}},
        {"id": "x", "attendees": ["bad", None, {}], "recurrence": None, "description": None},
    ],
)
def test_odd_items_never_raise(item):
    s = summarize(item)
    assert s.summary == ""
    d = parse_event(item, calendar_id=CAL, calendar_time_zone=CAL_TZ, editable=False, max_description_chars=5)
    assert d.summary == ""
