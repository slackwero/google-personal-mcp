import json
import logging
from datetime import date, datetime

import pytest

from google_personal_mcp.gcalendar.client import CalendarApiError
from google_personal_mcp.gcalendar.operations import (
    EventInput,
    create_event,
    delete_event,
    update_event,
    validate_recurrence,
)
from tests.fakes import FakeCalendarClient

SANTIAGO = "America/Santiago"


def own_event(event_id: str = "e1", **extra) -> dict:
    return {
        "id": event_id,
        "summary": "Dentist",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-04T10:00:00-03:00", "timeZone": SANTIAGO},
        "end": {"dateTime": "2026-11-04T11:00:00-03:00", "timeZone": SANTIAGO},
        "organizer": {"email": "user@example.com", "self": True},
        **extra,
    }


@pytest.fixture
def fake() -> FakeCalendarClient:
    return FakeCalendarClient()


def test_create_timed_event_body(fake):
    fields = EventInput(
        summary="Lunch",
        start=datetime(2026, 11, 5, 13, 0),
        end=datetime(2026, 11, 5, 14, 0),
        description="With Ana",
        location="Cafe",
    )
    event = create_event(fake, "primary", fields)
    body = fake.insert_calls[0]["body"]
    assert body == {
        "summary": "Lunch",
        "start": {"dateTime": "2026-11-05T13:00:00", "timeZone": SANTIAGO},
        "end": {"dateTime": "2026-11-05T14:00:00", "timeZone": SANTIAGO},
        "description": "With Ana",
        "location": "Cafe",
    }
    assert "attendees" not in body
    assert event.summary == "Lunch"


def test_create_all_day_defaults_end_to_next_day(fake):
    create_event(fake, "primary", EventInput(summary="Holiday", start=date(2026, 12, 31)))
    body = fake.insert_calls[0]["body"]
    assert body["start"] == {"date": "2026-12-31"}
    assert body["end"] == {"date": "2027-01-01"}


def test_create_recurring_event_always_has_a_time_zone(fake):
    fields = EventInput(
        summary="Standup",
        start=datetime(2026, 11, 5, 9, 0),
        end=datetime(2026, 11, 5, 9, 15),
        recurrence=["RRULE:FREQ=DAILY;COUNT=3"],
    )
    create_event(fake, "primary", fields)
    body = fake.insert_calls[0]["body"]
    assert body["start"]["timeZone"] == SANTIAGO
    assert body["end"]["timeZone"] == SANTIAGO
    assert body["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=3"]


def test_create_uses_the_given_time_zone(fake):
    fields = EventInput(
        summary="Call", start=datetime(2026, 11, 5, 9), end=datetime(2026, 11, 5, 10), time_zone="Europe/Madrid"
    )
    create_event(fake, "primary", fields)
    assert fake.insert_calls[0]["body"]["start"]["timeZone"] == "Europe/Madrid"


def test_create_needs_end_for_timed_events(fake):
    with pytest.raises(ValueError, match="end"):
        create_event(fake, "primary", EventInput(summary="x", start=datetime(2026, 11, 5, 9)))
    assert fake.insert_calls == []


def test_create_rejects_an_end_not_after_the_start(fake):
    fields = EventInput(summary="x", start=datetime(2026, 11, 5, 9), end=datetime(2026, 11, 5, 8))
    with pytest.raises(ValueError, match="after"):
        create_event(fake, "primary", fields)
    assert fake.insert_calls == []


def test_create_checks_a_mixed_naive_and_aware_pair_with_the_calendar_zone(fake):
    # 09:00 Santiago (-03:00 in November) is 12:00 UTC; the aware end 11:00 UTC is before it.
    fields = EventInput(summary="x", start=datetime(2026, 11, 5, 9), end=datetime.fromisoformat("2026-11-05T11:00:00Z"))
    with pytest.raises(ValueError, match="after"):
        create_event(fake, "primary", fields)
    assert fake.insert_calls == []


def test_create_refuses_a_calendar_not_owned(fake):
    fake.add_calendar("team@example.com", access_role="writer")
    fields = EventInput(summary="x", start=date(2026, 11, 5))
    with pytest.raises(ValueError, match="owns"):
        create_event(fake, "team@example.com", fields)
    assert fake.insert_calls == []


@pytest.mark.parametrize(
    "lines",
    [["FREQ=DAILY"], ["RRULE:FREQ=DAILY\nATTENDEE:x"], ["RRULE:FREQ=DAILY\rX"], ["RRULE:FREQ=DAILY"] * 11],
)
def test_recurrence_validation(lines):
    with pytest.raises(ValueError, match="recurrence|line"):
        validate_recurrence(lines)


def test_recurrence_validation_accepts_the_four_kinds():
    lines = ["RRULE:FREQ=DAILY", "EXRULE:FREQ=WEEKLY", "RDATE:20261105", "EXDATE:20261106"]
    assert validate_recurrence(lines) == lines
    assert validate_recurrence(["RRULE:FREQ=DAILY"] * 10) == ["RRULE:FREQ=DAILY"] * 10


def test_update_start_only_keeps_duration_and_reports_before(fake):
    fake.add_event("primary", own_event())
    result = update_event(fake, "primary", "e1", EventInput(start=datetime(2026, 11, 4, 15, 0)), series=False)
    patch = fake.patch_calls[0]
    assert patch["event_id"] == "e1"
    assert patch["body"] == {
        "start": {"dateTime": "2026-11-04T15:00:00", "timeZone": SANTIAGO},
        "end": {"dateTime": "2026-11-04T16:00:00", "timeZone": SANTIAGO},
    }
    assert result.before == {
        "start": "2026-11-04T10:00:00-03:00",
        "end": "2026-11-04T11:00:00-03:00",
        "time_zone": SANTIAGO,
    }
    assert result.event.id == "e1"


def test_update_reads_a_local_time_in_the_event_zone_before_the_calendar_zone(fake):
    new_york = "America/New_York"
    event = own_event()
    event["start"] = {"dateTime": "2026-11-04T10:00:00-05:00", "timeZone": new_york}
    event["end"] = {"dateTime": "2026-11-04T11:00:00-05:00", "timeZone": new_york}
    fake.add_event("primary", event)  # the primary calendar is in America/Santiago
    update_event(fake, "primary", "e1", EventInput(start=datetime(2026, 11, 4, 15, 0)), series=False)
    assert fake.patch_calls[0]["body"] == {
        "start": {"dateTime": "2026-11-04T15:00:00", "timeZone": new_york},
        "end": {"dateTime": "2026-11-04T16:00:00", "timeZone": new_york},
    }


def test_update_without_times_does_not_need_the_event_zone(fake):
    event = own_event()
    event["start"] = {**event["start"], "timeZone": "Invalid/Zone"}
    fake.add_event("primary", event)
    result = update_event(fake, "primary", "e1", EventInput(summary="Checkup"), series=False)
    assert fake.patch_calls[0]["body"] == {"summary": "Checkup"}
    assert result.before == {"summary": "Dentist"}


def test_update_all_day_start_only_shifts_by_days(fake):
    event = own_event()
    event["start"] = {"date": "2026-11-03"}
    event["end"] = {"date": "2026-11-05"}
    fake.add_event("primary", event)
    result = update_event(fake, "primary", "e1", EventInput(start=date(2026, 11, 10)), series=False)
    assert fake.patch_calls[0]["body"] == {"start": {"date": "2026-11-10"}, "end": {"date": "2026-11-12"}}
    assert result.before == {"start": "2026-11-03", "end": "2026-11-05"}


def test_update_end_only_checks_against_the_old_start(fake):
    fake.add_event("primary", own_event())
    with pytest.raises(ValueError, match="after"):
        update_event(fake, "primary", "e1", EventInput(end=datetime(2026, 11, 4, 9, 0)), series=False)
    assert fake.patch_calls == []
    update_event(fake, "primary", "e1", EventInput(end=datetime(2026, 11, 4, 12, 0)), series=False)
    assert list(fake.patch_calls[0]["body"]) == ["end"]


def test_update_switching_kind_without_end_is_an_error(fake):
    fake.add_event("primary", own_event())
    with pytest.raises(ValueError, match="end"):
        update_event(fake, "primary", "e1", EventInput(start=date(2026, 11, 10)), series=False)
    assert fake.patch_calls == []


def test_update_switching_kind_with_end_clears_the_other_key(fake):
    fake.add_event("primary", own_event())
    changes = EventInput(start=date(2026, 11, 10), end=date(2026, 11, 11))
    update_event(fake, "primary", "e1", changes, series=False)
    body = fake.patch_calls[0]["body"]
    assert body["start"] == {"date": "2026-11-10", "dateTime": None, "timeZone": None}
    assert body["end"] == {"date": "2026-11-11", "dateTime": None, "timeZone": None}


def test_update_empty_string_clears_description(fake):
    fake.add_event("primary", own_event(description="old text"))
    result = update_event(fake, "primary", "e1", EventInput(description=""), series=False)
    assert fake.patch_calls[0]["body"] == {"description": ""}
    assert result.before == {"description": "old text"}


def test_update_before_uses_empty_strings_for_absent_text(fake):
    fake.add_event("primary", own_event())
    result = update_event(fake, "primary", "e1", EventInput(description="new", location="Room"), series=False)
    assert result.before == {"description": "", "location": ""}


def test_update_with_nothing_to_change_is_an_error(fake):
    fake.add_event("primary", own_event())
    with pytest.raises(ValueError, match="at least one"):
        update_event(fake, "primary", "e1", EventInput(), series=False)
    assert fake.patch_calls == []


def test_update_time_zone_alone_is_an_error(fake):
    fake.add_event("primary", own_event())
    with pytest.raises(ValueError, match="time_zone"):
        update_event(fake, "primary", "e1", EventInput(time_zone="Europe/Madrid"), series=False)
    assert fake.patch_calls == []


def test_update_recurrence_on_an_occurrence_is_an_error(fake):
    fake.add_event("primary", own_event("e1_20261104T130000Z", recurringEventId="e1"))
    changes = EventInput(recurrence=["RRULE:FREQ=DAILY"])
    with pytest.raises(ValueError, match="occurrence"):
        update_event(fake, "primary", "e1_20261104T130000Z", changes, series=False)
    assert fake.patch_calls == []


def test_update_series_needs_the_flag(fake):
    fake.add_event("primary", own_event(recurrence=["RRULE:FREQ=DAILY"], iCalUID="e1@google.com"))
    with pytest.raises(ValueError, match="series"):
        update_event(fake, "primary", "e1", EventInput(summary="x"), series=False)
    update_event(fake, "primary", "e1", EventInput(summary="x"), series=True)
    assert len(fake.patch_calls) == 1


def test_update_refuses_a_calendar_not_owned(fake):
    fake.add_calendar("team@example.com", access_role="writer")
    fake.add_event("team@example.com", own_event())
    with pytest.raises(ValueError, match="owns"):
        update_event(fake, "team@example.com", "e1", EventInput(summary="x"), series=False)
    assert fake.patch_calls == []


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_guards_block_events_with_guests(fake, operation):
    guest = {"email": "ana@example.com", "responseStatus": "accepted"}
    fake.add_event("primary", own_event(attendees=[guest]))
    with pytest.raises(ValueError, match="guests"):
        if operation == "update":
            update_event(fake, "primary", "e1", EventInput(summary="x"), series=False)
        else:
            delete_event(fake, "primary", "e1", series=False)
    assert fake.patch_calls == []
    assert fake.delete_calls == []


def test_delete_blocks_a_cancelled_event(fake):
    fake.add_event("primary", own_event(status="cancelled"))
    with pytest.raises(ValueError, match="cancelled"):
        delete_event(fake, "primary", "e1", series=False)
    assert fake.delete_calls == []


def test_delete_series_needs_the_flag_and_returns_the_snapshot(fake):
    fake.add_event(
        "primary", own_event(description="x" * 6_000, recurrence=["RRULE:FREQ=DAILY;COUNT=3"], iCalUID="e1@google.com")
    )
    with pytest.raises(ValueError, match="series"):
        delete_event(fake, "primary", "e1", series=False)
    assert fake.delete_calls == []
    snapshot = delete_event(fake, "primary", "e1", series=True)
    assert fake.delete_calls == [{"calendar_id": "user@example.com", "event_id": "e1"}]
    assert snapshot.recurrence == ["RRULE:FREQ=DAILY;COUNT=3"]
    assert snapshot.description == "x" * 6_000
    assert snapshot.summary == "Dentist"


GUEST = {"email": "ana@example.com", "responseStatus": "accepted"}


def series_event(**extra) -> dict:
    return own_event("s1", recurrence=["RRULE:FREQ=DAILY;COUNT=3"], iCalUID="s1@google.com", **extra)


def edited_occurrence(**extra) -> dict:
    return own_event(
        "s1_20261105T130000Z",
        recurringEventId="s1",
        iCalUID="s1@google.com",
        originalStartTime={"dateTime": "2026-11-05T10:00:00-03:00", "timeZone": SANTIAGO},
        **extra,
    )


def write_series(fake: FakeCalendarClient, operation: str, before_write=lambda: None) -> object:
    if operation == "update":
        return update_event(fake, "primary", "s1", EventInput(summary="x"), series=True, before_write=before_write)
    return delete_event(fake, "primary", "s1", series=True, before_write=before_write)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_series_write_is_refused_when_an_edited_occurrence_has_guests(fake, operation):
    fake.add_event("primary", series_event())
    fake.add_event("primary", edited_occurrence(attendees=[GUEST]))
    hooks: list[str] = []
    with pytest.raises(ValueError, match="1 occurrence of this series") as exc:
        write_series(fake, operation, before_write=lambda: hooks.append("before_write"))
    assert "guests" in str(exc.value)
    assert hooks == []
    assert fake.patch_calls == fake.delete_calls == []
    assert fake.series_exception_calls == [{"calendar_id": "user@example.com", "ical_uid": "s1@google.com"}]


def test_series_write_counts_every_blocking_occurrence(fake):
    fake.add_event("primary", series_event())
    fake.add_event("primary", edited_occurrence(attendees=[GUEST]))
    fake.add_event("primary", {**edited_occurrence(locked=True), "id": "s1_20261106T130000Z"})
    with pytest.raises(ValueError, match="2 occurrences of this series") as exc:
        write_series(fake, "delete")
    assert "guests" in str(exc.value) and "locked" in str(exc.value)
    assert fake.delete_calls == []


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_series_write_ignores_a_cancelled_occurrence_with_guests(fake, operation):
    fake.add_event("primary", series_event())
    fake.add_event("primary", edited_occurrence(status="cancelled", attendees=[GUEST]))
    write_series(fake, operation)
    assert len(fake.patch_calls) + len(fake.delete_calls) == 1


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_series_write_without_edited_occurrences_passes(fake, operation):
    fake.add_event("primary", series_event())
    write_series(fake, operation)
    assert len(fake.patch_calls) + len(fake.delete_calls) == 1


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_series_write_without_an_ical_uid_is_refused(fake, operation):
    event = series_event()
    del event["iCalUID"]
    fake.add_event("primary", event)
    with pytest.raises(ValueError, match="iCalUID"):
        write_series(fake, operation)
    assert fake.patch_calls == fake.delete_calls == []
    assert fake.series_exception_calls == []


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_series_write_stops_when_the_occurrence_listing_fails(fake, operation):
    fake.add_event("primary", series_event())
    fake.method_errors["list_series_exceptions"] = CalendarApiError(500, "Backend Error")
    with pytest.raises(CalendarApiError):
        write_series(fake, operation)
    assert fake.patch_calls == fake.delete_calls == []


def test_series_write_refuses_an_unreadable_listed_item(fake):
    fake.add_event("primary", series_event())
    fake.list_series_exceptions = lambda calendar_id, ical_uid: [series_event(), "oops"]  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="could not be read"):
        write_series(fake, "delete")
    assert fake.delete_calls == []


def test_occurrence_write_does_not_list_the_series(fake):
    fake.add_event("primary", series_event())
    fake.add_event("primary", edited_occurrence())
    delete_event(fake, "primary", "s1_20261105T130000Z", series=False)
    assert fake.series_exception_calls == []


def test_delete_snapshot_cuts_the_description_at_100000(fake):
    fake.add_event("primary", own_event(description="y" * 100_001))
    snapshot = delete_event(fake, "primary", "e1", series=False)
    assert len(snapshot.description) == 100_000
    assert snapshot.description_truncated is True


class Stop(Exception):
    pass


def stop() -> None:
    raise Stop


def test_before_write_hook_stops_the_write(fake):
    fake.add_event("primary", own_event())
    created = EventInput(summary="x", start=date(2026, 11, 5))
    with pytest.raises(Stop):
        create_event(fake, "primary", created, before_write=stop)
    with pytest.raises(Stop):
        update_event(fake, "primary", "e1", EventInput(summary="y"), series=False, before_write=stop)
    with pytest.raises(Stop):
        delete_event(fake, "primary", "e1", series=False, before_write=stop)
    assert fake.insert_calls == fake.patch_calls == fake.delete_calls == []


def test_write_log_has_only_the_id(fake, caplog):
    fake.add_event("primary", own_event("e1", description="secret notes"))
    caplog.set_level(logging.INFO, logger="google_personal_mcp.gcalendar.operations")
    update_event(fake, "primary", "e1", EventInput(start=datetime(2026, 11, 4, 15, 0)), series=False)
    delete_event(fake, "primary", "e1", series=False)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        f"calendar_update_event wrote event: id={json.dumps('e1')}",
        f"calendar_delete_event wrote event: id={json.dumps('e1')}",
    ]
