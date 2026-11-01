import pytest

from google_personal_mcp.gcalendar.guards import (
    calendar_is_writable,
    check_editable,
    check_series_flag,
    check_writable_calendar,
    edit_blockers,
)


def own_event(**extra) -> dict:
    return {
        "id": "e",
        "status": "confirmed",
        "eventType": "default",
        "organizer": {"email": "user@example.com", "self": True},
        **extra,
    }


def test_own_event_has_no_blockers():
    assert edit_blockers(own_event()) == []
    assert edit_blockers(own_event(attendees=[{"email": "me@example.com", "self": True}])) == []
    check_editable(own_event())


@pytest.mark.parametrize(
    "extra, reason",
    [
        ({"eventType": "birthday"}, "type"),
        ({"attendees": [{"email": "a@example.com"}]}, "guests"),
        ({"status": "cancelled"}, "cancelled"),
        ({"locked": True}, "locked"),
        ({"organizer": {"email": "a@example.com"}}, "organized"),
    ],
)
def test_each_blocker(extra, reason):
    blockers = edit_blockers(own_event(**extra))
    assert len(blockers) == 1
    assert reason in blockers[0]
    with pytest.raises(ValueError, match=reason):
        check_editable(own_event(**extra))


def test_check_editable_joins_reasons():
    with pytest.raises(ValueError) as exc:
        check_editable(own_event(status="cancelled", locked=True))
    assert "cancelled" in str(exc.value) and "locked" in str(exc.value)


def test_calendar_roles():
    assert calendar_is_writable({"accessRole": "owner"}) is True
    check_writable_calendar({"accessRole": "owner"})
    for role in ("writer", "reader", "freeBusyReader"):
        assert calendar_is_writable({"accessRole": role}) is False
        with pytest.raises(ValueError, match=role) as exc:
            check_writable_calendar({"accessRole": role})
        assert "owns" in str(exc.value)
    assert calendar_is_writable({}) is False


def test_series_flag_rules():
    master = {"id": "s", "recurrence": ["RRULE:FREQ=DAILY"]}
    occurrence = {"id": "s_20261103T130000Z", "recurringEventId": "s"}
    single = {"id": "e"}
    check_series_flag(master, series=True)
    check_series_flag(occurrence, series=False)
    check_series_flag(single, series=False)
    with pytest.raises(ValueError, match="series: true") as exc:
        check_series_flag(master, series=False)
    assert "calendar_list_events" in str(exc.value)
    for event in (occurrence, single):
        with pytest.raises(ValueError, match="recurring series"):
            check_series_flag(event, series=True)


@pytest.mark.parametrize("organizer", ["missing", "oops", None, ["x"]])
def test_unknown_organizer_blocks(organizer):
    event = own_event()
    if organizer == "missing":
        del event["organizer"]
    else:
        event["organizer"] = organizer
    blockers = edit_blockers(event)
    assert len(blockers) == 1
    assert "organizer" in blockers[0]


@pytest.mark.parametrize("attendees", ["oops", {"email": "a@example.com"}, None])
def test_unreadable_attendee_list_blocks(attendees):
    blockers = edit_blockers(own_event(attendees=attendees))
    assert len(blockers) == 1
    assert "attendee" in blockers[0]
