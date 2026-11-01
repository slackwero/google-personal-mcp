from datetime import date, datetime, timedelta

import pytest

from google_personal_mcp.gcalendar.times import (
    all_day_default_end,
    check_span,
    event_time,
    parse_time,
    shift_end,
    to_rfc3339,
    zone,
)

SANTIAGO = "America/Santiago"


def test_parse_time_forms():
    assert parse_time("2026-11-03") == date(2026, 11, 3)
    assert parse_time("2026-11-03T10:00") == datetime(2026, 11, 3, 10, 0)
    assert parse_time("2026-11-03T10:00:30") == datetime(2026, 11, 3, 10, 0, 30)
    assert parse_time("2026-11-03T10:00:00-03:00").utcoffset() == timedelta(hours=-3)
    assert parse_time("2026-11-03T10:00:00Z").utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "bad", ["", "tomorrow", "2026-13-01", "03/11/2026", "2026-W45-2", "20261103", "20261103T1000", "2026-11-03 10:00"]
)
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError, match="2026-11-03T10:00"):
        parse_time(bad)


@pytest.mark.parametrize("bad", ["Mars/Olympus", ""])
def test_zone_rejects_unknown_or_empty(bad):
    with pytest.raises(ValueError, match="time zone"):
        zone(bad)


def test_zone_error_names_the_input():
    with pytest.raises(ValueError, match="Mars/Olympus"):
        zone("Mars/Olympus")


def test_to_rfc3339_uses_the_calendar_zone():
    tz = zone(SANTIAGO)
    assert to_rfc3339(parse_time("2026-11-03T10:00"), tz) == "2026-11-03T10:00:00-03:00"
    assert to_rfc3339(date(2026, 11, 3), tz) == "2026-11-03T00:00:00-03:00"
    assert to_rfc3339(parse_time("2026-11-03T10:00:00Z"), tz) == "2026-11-03T10:00:00+00:00"


def test_nonexistent_local_hour_does_not_raise():  # Chile springs forward at 00:00 on 2026-09-06
    assert to_rfc3339(parse_time("2026-09-06T00:30"), zone(SANTIAGO)).startswith("2026-09-06T00:30:00")


def test_event_time_bodies():
    assert event_time(date(2026, 11, 3), SANTIAGO) == {"date": "2026-11-03"}
    assert event_time(datetime(2026, 11, 3, 10), SANTIAGO) == {"dateTime": "2026-11-03T10:00:00", "timeZone": SANTIAGO}


def test_check_span_rejects_mixed_forms_and_reversed_ranges():
    tz = zone(SANTIAGO)
    with pytest.raises(ValueError, match="all-day"):
        check_span(date(2026, 11, 3), datetime(2026, 11, 4, 10), tz)
    with pytest.raises(ValueError, match="all-day"):
        check_span(datetime(2026, 11, 3, 10), date(2026, 11, 4), tz)
    with pytest.raises(ValueError, match="end"):
        check_span(datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 10), tz)
    with pytest.raises(ValueError, match="end"):
        check_span(date(2026, 11, 3), date(2026, 11, 3), tz)
    with pytest.raises(ValueError, match="end"):
        check_span(parse_time("2026-11-03T10:00"), parse_time("2026-11-03T12:00:00Z"), tz)  # 09:00 local
    check_span(datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11), tz)
    check_span(date(2026, 11, 3), date(2026, 11, 4), tz)


def test_all_day_default_end():
    assert all_day_default_end(date(2026, 11, 3)) == date(2026, 11, 4)


def test_shift_end_keeps_duration():
    tz = zone(SANTIAGO)
    timed = ({"dateTime": "2026-11-03T10:00:00-03:00"}, {"dateTime": "2026-11-03T11:30:00-03:00"})
    assert shift_end(*timed, datetime(2026, 11, 3, 15), tz) == datetime(2026, 11, 3, 16, 30)
    assert shift_end({"date": "2026-11-03"}, {"date": "2026-11-05"}, date(2026, 11, 10), tz) == date(2026, 11, 12)
    with pytest.raises(ValueError, match="end"):
        shift_end(*timed, date(2026, 11, 10), tz)
    with pytest.raises(ValueError, match="end"):
        shift_end({"date": "2026-11-03"}, {"date": "2026-11-05"}, datetime(2026, 11, 10, 9), tz)


@pytest.mark.parametrize(
    ("old_start", "old_end"),
    [
        ({}, {"dateTime": "2026-11-03T11:00:00-03:00"}),
        ({"dateTime": "2026-11-03T10:00:00-03:00"}, {"dateTime": 5}),
        ({"dateTime": None}, {"date": "2026-11-05"}),
        ({"date": "2026-11-03"}, {"date": "not a date"}),
    ],
)
def test_shift_end_reports_unreadable_google_times_as_value_errors(old_start, old_end):
    with pytest.raises(ValueError, match="event"):
        shift_end(old_start, old_end, datetime(2026, 11, 3, 15), zone(SANTIAGO))
