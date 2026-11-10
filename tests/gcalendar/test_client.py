import pytest

from google_personal_mcp.api_errors import GoogleApiError
from google_personal_mcp.gcalendar.client import PAGE_MAX, CalendarApiError, CalendarClient
from google_personal_mcp.gmail.client import GmailApiError
from google_personal_mcp.quota import QuotaLimiter
from tests.fakes import FakeService, http_error


class RecordingLimiter:
    def __init__(self) -> None:
        self.acquired: list[int] = []

    def acquire(self, units: int) -> None:
        self.acquired.append(units)


def make_client(service: FakeService) -> tuple[CalendarClient, RecordingLimiter]:
    limiter = RecordingLimiter()
    return CalendarClient(service, limiter), limiter  # type: ignore[arg-type]


def test_client_carries_the_account_it_was_built_for():
    assert CalendarClient(FakeService(), QuotaLimiter(), account="user@example.com").account == "user@example.com"
    assert CalendarClient(FakeService(), QuotaLimiter()).account is None


def test_primary_calendar_id_reads_the_primary_entry():
    service = FakeService({"calendarList.get": {"id": "user@example.com"}})
    assert CalendarClient(service, QuotaLimiter()).get_primary_calendar_id() == "user@example.com"
    assert service.calls_to("calendarList.get") == [{"calendarId": "primary"}]


def test_list_calendars_reads_every_page():
    pages = {None: {"items": [{"id": "a"}], "nextPageToken": "t2"}, "t2": {"items": [{"id": "b"}]}}
    service = FakeService({"calendarList.list": lambda kw: pages[kw.get("pageToken")]})
    client, _ = make_client(service)
    assert client.list_calendars() == [{"id": "a"}, {"id": "b"}]
    assert service.calls_to("calendarList.list") == [
        {"maxResults": PAGE_MAX},
        {"maxResults": PAGE_MAX, "pageToken": "t2"},
    ]


def test_get_calendar_entry_reads_the_given_calendar():
    service = FakeService({"calendarList.get": {"id": "work", "timeZone": "America/Santiago"}})
    client, _ = make_client(service)
    assert client.get_calendar_entry("work")["timeZone"] == "America/Santiago"
    assert service.calls_to("calendarList.get") == [{"calendarId": "work"}]


def test_list_events_expands_recurring_events():
    service = FakeService({"events.list": {"items": [], "timeZone": "America/Santiago"}})
    client, _ = make_client(service)
    response = client.list_events(
        "primary", time_min="2026-11-01T00:00:00Z", time_max="2026-11-02T00:00:00Z", max_results=10
    )
    assert response == {"items": [], "timeZone": "America/Santiago"}
    assert service.calls_to("events.list") == [
        {
            "calendarId": "primary",
            "timeMin": "2026-11-01T00:00:00Z",
            "timeMax": "2026-11-02T00:00:00Z",
            "maxResults": 10,
            "singleEvents": True,
            "orderBy": "startTime",
        }
    ]


def test_list_events_sends_query_and_page_token_when_given():
    service = FakeService({"events.list": {}})
    client, _ = make_client(service)
    client.list_events("primary", time_min="a", time_max="b", max_results=5, query="dentist", page_token="t")  # noqa: S106
    call = service.calls_to("events.list")[0]
    assert call["q"] == "dentist" and call["pageToken"] == "t"


def test_list_series_exceptions_reads_every_page_of_the_series():
    pages = {
        None: {"items": [{"id": "s1"}], "nextPageToken": "t2"},
        "t2": {"items": [{"id": "s1_20261105T130000Z", "recurringEventId": "s1"}]},
    }
    service = FakeService({"events.list": lambda kw: pages[kw.get("pageToken")]})
    client, limiter = make_client(service)
    items = client.list_series_exceptions("primary", "s1@google.com")
    assert items == [{"id": "s1"}, {"id": "s1_20261105T130000Z", "recurringEventId": "s1"}]
    first = {
        "calendarId": "primary",
        "iCalUID": "s1@google.com",
        "singleEvents": False,
        "showDeleted": False,
        "maxResults": PAGE_MAX,
    }
    assert service.calls_to("events.list") == [first, {**first, "pageToken": "t2"}]
    assert limiter.acquired == [1, 1]


def test_get_event_reads_one_event():
    service = FakeService({"events.get": {"id": "e1"}})
    client, _ = make_client(service)
    assert client.get_event("primary", "e1") == {"id": "e1"}
    assert service.calls_to("events.get") == [{"calendarId": "primary", "eventId": "e1"}]


def test_writes_never_notify_anyone():
    service = FakeService({"events.insert": {"id": "e1"}, "events.patch": {"id": "e1"}, "events.delete": ""})
    client, _ = make_client(service)
    client.insert_event("primary", {"summary": "x"})
    client.patch_event("primary", "e1", {"summary": "y"})
    client.delete_event("primary", "e1")
    assert service.calls_to("events.insert") == [
        {"calendarId": "primary", "body": {"summary": "x"}, "sendUpdates": "none"}
    ]
    assert service.calls_to("events.patch") == [
        {"calendarId": "primary", "eventId": "e1", "body": {"summary": "y"}, "sendUpdates": "none"}
    ]
    assert service.calls_to("events.delete") == [{"calendarId": "primary", "eventId": "e1", "sendUpdates": "none"}]


@pytest.mark.parametrize("method", ["insert_event", "patch_event"])
def test_attendees_are_refused_before_any_request(method):
    service = FakeService()
    client, limiter = make_client(service)
    args = ("primary", {"attendees": [{"email": "a@example.com"}]})
    if method == "patch_event":
        args = ("primary", "e1", args[1])
    with pytest.raises(ValueError, match="attendees"):
        getattr(client, method)(*args)
    assert service.calls == [] and limiter.acquired == []


def test_each_request_costs_one_unit():
    service = FakeService({"events.get": {"id": "e1"}, "events.delete": ""})
    client, limiter = make_client(service)
    client.get_event("primary", "e1")
    client.delete_event("primary", "e1")
    assert limiter.acquired == [1, 1]


def test_execute_uses_retries():
    service = FakeService({"events.get": {"id": "e1"}, "calendarList.get": {"id": "p"}})
    client, _ = make_client(service)
    client.get_event("primary", "e1")
    client.get_primary_calendar_id()
    assert [e["num_retries"] for e in service.executes] == [3, 3]


def test_http_404_becomes_readable_error():
    client = CalendarClient(FakeService(error=http_error(404, "Not Found")), QuotaLimiter())
    with pytest.raises(CalendarApiError, match="Calendar API error 404: Not Found") as exc:
        client.get_event("primary", "e1")
    assert exc.value.status == 404 and isinstance(exc.value, GoogleApiError)


def test_freebusy_body():
    service = FakeService({"freebusy.query": {"calendars": {}}})
    client, _ = make_client(service)
    client.query_freebusy(time_min="t1", time_max="t2", calendar_ids=["a", "b"], time_zone="America/Santiago")
    assert service.calls_to("freebusy.query") == [
        {
            "body": {
                "timeMin": "t1",
                "timeMax": "t2",
                "timeZone": "America/Santiago",
                "items": [{"id": "a"}, {"id": "b"}],
            }
        }
    ]


def test_gmail_error_keeps_its_message():
    assert str(GmailApiError(404, "x")) == "Gmail API error 404: x"
    assert isinstance(GmailApiError(1, "y"), GoogleApiError)
