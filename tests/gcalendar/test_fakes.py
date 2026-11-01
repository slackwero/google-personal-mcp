import inspect

import pytest

from google_personal_mcp.gcalendar.client import CalendarApiError, CalendarClient
from tests.fakes import FakeCalendarClient


def _public(cls: type) -> dict[str, inspect.Signature]:
    return {n: inspect.signature(f) for n, f in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def test_fake_mirrors_the_real_client_surface():
    real, fake = _public(CalendarClient), _public(FakeCalendarClient)
    assert set(fake) - set(real) == {"add_calendar", "add_event"}  # the only test-only helpers
    for name, signature in real.items():
        if name == "from_credentials":
            continue
        assert name in fake, name
        assert list(fake[name].parameters) == list(signature.parameters), name


def test_fake_resolves_primary_and_round_trips_events():
    fake = FakeCalendarClient()
    created = fake.insert_event("primary", {"summary": "Dentist"})
    assert fake.get_event("user@example.com", created["id"])["summary"] == "Dentist"
    assert fake.patch_event("primary", created["id"], {"location": "Clinic"}) == {
        "id": created["id"],
        "summary": "Dentist",
        "location": "Clinic",
    }
    fake.delete_event("primary", created["id"])
    with pytest.raises(CalendarApiError, match="404"):
        fake.get_event("primary", created["id"])
    assert fake.insert_calls[0]["calendar_id"] == "user@example.com"
    assert fake.patch_calls[0]["event_id"] == created["id"]
    assert fake.delete_calls[0]["event_id"] == created["id"]


def test_fake_list_events_returns_insertion_order_and_time_zone():
    fake = FakeCalendarClient()
    fake.add_calendar("work", time_zone="Europe/Madrid")
    fake.add_event("work", {"id": "b"})
    fake.add_event("work", {"id": "a"})
    response = fake.list_events("work", time_min="t1", time_max="t2", max_results=10)
    assert [e["id"] for e in response["items"]] == ["b", "a"]
    assert response["timeZone"] == "Europe/Madrid"
    assert fake.list_calls[0]["calendar_id"] == "work"


def test_fake_list_series_exceptions_matches_the_ical_uid_in_insertion_order():
    fake = FakeCalendarClient()
    fake.add_event("primary", {"id": "s1_x", "iCalUID": "s1@google.com", "recurringEventId": "s1"})
    fake.add_event("primary", {"id": "other", "iCalUID": "other@google.com"})
    fake.add_event("primary", {"id": "s1", "iCalUID": "s1@google.com"})
    assert [e["id"] for e in fake.list_series_exceptions("primary", "s1@google.com")] == ["s1_x", "s1"]
    assert fake.series_exception_calls == [{"calendar_id": "user@example.com", "ical_uid": "s1@google.com"}]


def test_fake_freebusy_returns_scripted_busy_and_scripted_errors():
    fake = FakeCalendarClient()
    fake.busy["user@example.com"] = [{"start": "s", "end": "e"}]
    result = fake.query_freebusy(time_min="t1", time_max="t2", calendar_ids=["primary"], time_zone="UTC")
    assert result["calendars"]["user@example.com"]["busy"] == [{"start": "s", "end": "e"}]
    assert fake.freebusy_calls[0]["calendar_ids"] == ["user@example.com"]
    fake.method_errors["list_calendars"] = CalendarApiError(403, "Forbidden")
    with pytest.raises(CalendarApiError):
        fake.list_calendars()
