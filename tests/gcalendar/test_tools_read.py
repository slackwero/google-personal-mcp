from datetime import UTC, datetime

import pytest
from mcp.client import Client
from mcp.server import MCPServer

from google_personal_mcp.gcalendar.tools import CALENDAR_UNTRUSTED_NOTE, register_calendar_tools
from tests.fakes import FakeCalendarClient

pytestmark = pytest.mark.anyio

FIXED_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
READ_TOOLS = {"calendar_list_calendars", "calendar_list_events", "calendar_get_event", "calendar_freebusy"}


def own_event(event_id: str = "e1") -> dict:
    return {
        "id": event_id,
        "summary": "Dentist",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-04T10:00:00-03:00"},
        "end": {"dateTime": "2026-11-04T11:00:00-03:00"},
        "organizer": {"email": "user@example.com", "self": True},
    }


@pytest.fixture
def fake() -> FakeCalendarClient:
    return FakeCalendarClient()


def connect(fake: FakeCalendarClient) -> Client:
    server = MCPServer("test")
    register_calendar_tools(server, lambda: fake, now=lambda: FIXED_NOW)
    return Client(server)


async def test_read_tools_are_read_only_and_untrusted(fake):
    async with connect(fake) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools if t.name in READ_TOOLS}
    assert set(tools) == READ_TOOLS
    for tool in tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.description.endswith(CALENDAR_UNTRUSTED_NOTE)


async def test_list_calendars_marks_only_owned_writable(fake):
    fake.add_calendar("team@example.com", access_role="reader", summary="Team")
    fake.entries["team@example.com"]["summaryOverride"] = "My team"
    async with connect(fake) as c:
        r = await c.call_tool("calendar_list_calendars", {})
    calendars = {x["id"]: x for x in r.structured_content["result"]}
    assert calendars["user@example.com"]["writable"] is True
    assert calendars["user@example.com"]["primary"] is True
    assert calendars["team@example.com"]["writable"] is False
    assert calendars["team@example.com"]["summary"] == "My team"
    assert calendars["team@example.com"]["access_role"] == "reader"
    assert calendars["team@example.com"]["time_zone"] == "America/Santiago"


async def test_list_events_defaults_to_the_next_seven_days(fake):
    async with connect(fake) as c:
        r = await c.call_tool("calendar_list_events", {})
    assert not r.is_error
    call = fake.list_calls[0]
    assert call["time_min"] == "2026-11-03T12:00:00+00:00"
    assert call["time_max"] == "2026-11-10T12:00:00+00:00"
    assert call["max_results"] == 50
    assert r.structured_content["time_zone"] == "America/Santiago"


async def test_list_events_reads_local_bounds_in_the_calendar_zone(fake):
    async with connect(fake) as c:
        r = await c.call_tool(
            "calendar_list_events", {"time_min": "2026-11-03T09:00", "time_max": "2026-11-04", "query": "x"}
        )
    assert not r.is_error
    call = fake.list_calls[0]
    assert call["time_min"] == "2026-11-03T09:00:00-03:00"
    assert call["time_max"] == "2026-11-04T00:00:00-03:00"
    assert call["query"] == "x"
    assert r.structured_content["time_min"] == call["time_min"]
    assert r.structured_content["time_max"] == call["time_max"]


def recording_server(touched: list[str]) -> MCPServer:
    """A server whose client records and refuses every use, so a test can prove no call happened."""

    class Exploding:
        def __getattr__(self, name):
            touched.append(name)
            raise AssertionError(f"client used: {name}")

    def provider():
        touched.append("client_provider")
        return Exploding()

    server = MCPServer("test")
    register_calendar_tools(server, provider, now=lambda: FIXED_NOW)
    return server


REVERSED = "time_max must be after time_min"


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("calendar_list_events", {"time_min": "2026-11-05", "time_max": "2026-11-04"}, REVERSED),
        ("calendar_list_events", {"time_min": "2026-11-05T10:00", "time_max": "2026-11-05"}, REVERSED),
        ("calendar_list_events", {"time_min": "2026-11-05T10:00Z", "time_max": "2026-11-05T10:00Z"}, REVERSED),
        ("calendar_list_events", {"time_min": "tomorrow"}, "Invalid time 'tomorrow'"),
        ("calendar_list_events", {"time_max": "2026-13-01"}, "Invalid time '2026-13-01'"),
        ("calendar_freebusy", {"time_min": "2026-11-05", "time_max": "2026-11-04"}, REVERSED),
        ("calendar_freebusy", {"time_min": "2026-11-05T10:00Z", "time_max": "2026-11-05T09:00+00:00"}, REVERSED),
        ("calendar_freebusy", {"time_min": "bad", "time_max": "2026-11-04"}, "Invalid time 'bad'"),
        ("calendar_freebusy", {"time_min": "2026-11-04", "time_max": ""}, "Invalid time ''"),
    ],
)
async def test_read_tools_reject_bad_bounds_before_calling_google(tool, args, message):
    touched: list[str] = []
    async with Client(recording_server(touched)) as c:
        r = await c.call_tool(tool, args)
    assert r.is_error
    assert touched == []
    assert message in r.content[0].text


@pytest.mark.parametrize("tool", ["calendar_list_events", "calendar_freebusy"])
async def test_a_range_that_needs_the_calendar_zone_is_checked_after_the_calendar_read(fake, tool):
    # Midnight on 5 November in Santiago (-03:00) is 03:00 UTC, after the aware time_max.
    async with connect(fake) as c:
        r = await c.call_tool(tool, {"time_min": "2026-11-05", "time_max": "2026-11-05T02:00:00Z"})
    assert r.is_error
    assert REVERSED in r.content[0].text
    assert fake.list_calls == fake.freebusy_calls == []


async def test_list_events_says_to_pass_the_bounds_back_when_paging(fake):
    async with connect(fake) as c:
        tool = next(t for t in (await c.list_tools()).tools if t.name == "calendar_list_events")
    assert "When paging with page_token, pass back the returned time_min and time_max." in tool.description


async def test_list_events_flags_editable_events(fake):
    guest = own_event("e2")
    guest["attendees"] = [{"email": "other@example.com"}]
    fake.add_event("primary", own_event("e1"))
    fake.add_event("primary", guest)
    fake.add_calendar("team@example.com", access_role="reader")
    fake.add_event("team@example.com", own_event("e3"))
    async with connect(fake) as c:
        mine = await c.call_tool("calendar_list_events", {})
        theirs = await c.call_tool("calendar_list_events", {"calendar_id": "team@example.com"})
    assert [(e["id"], e["editable"]) for e in mine.structured_content["events"]] == [("e1", True), ("e2", False)]
    assert [e["editable"] for e in theirs.structured_content["events"]] == [False]
    assert mine.structured_content["events"][0]["calendar_id"] == "user@example.com"


async def test_get_event_returns_detail(fake):
    fake.add_event("primary", {**own_event(), "description": "abcdef"})
    async with connect(fake) as c:
        r = await c.call_tool("calendar_get_event", {"event_id": "e1", "max_description_chars": 3})
    assert r.structured_content["description"] == "abc"
    assert r.structured_content["description_truncated"] is True
    assert r.structured_content["editable"] is True


async def test_get_event_on_the_wrong_calendar_is_a_readable_error(fake):
    fake.add_calendar("team@example.com")
    fake.add_event("primary", own_event())
    async with connect(fake) as c:
        r = await c.call_tool("calendar_get_event", {"event_id": "e1", "calendar_id": "team@example.com"})
    assert r.is_error
    assert "Calendar API error 404" in r.content[0].text


async def test_freebusy_returns_busy_and_errors_per_calendar(fake):
    fake.busy["user@example.com"] = [{"start": "2026-11-04T10:00:00-03:00", "end": "2026-11-04T11:00:00-03:00"}]
    async with connect(fake) as c:
        r = await c.call_tool("calendar_freebusy", {"time_min": "2026-11-04T00:00", "time_max": "2026-11-05T00:00"})
    assert not r.is_error
    assert r.structured_content["time_zone"] == "America/Santiago"
    assert r.structured_content["calendars"]["user@example.com"] == {
        "busy": [{"start": "2026-11-04T10:00:00-03:00", "end": "2026-11-04T11:00:00-03:00"}],
        "errors": [],
    }
    assert fake.freebusy_calls[0]["time_min"] == "2026-11-04T00:00:00-03:00"
    assert fake.freebusy_calls[0]["time_zone"] == "America/Santiago"


async def test_freebusy_maps_google_errors_to_reasons(fake):
    fake.query_freebusy = lambda **_: {  # type: ignore[method-assign]
        "calendars": {"x@example.com": {"errors": [{"domain": "global", "reason": "notFound"}]}}
    }
    async with connect(fake) as c:
        r = await c.call_tool(
            "calendar_freebusy",
            {"time_min": "2026-11-04", "time_max": "2026-11-05", "calendar_ids": ["x@example.com"]},
        )
    assert r.structured_content["calendars"]["x@example.com"] == {"busy": [], "errors": ["notFound"]}


async def test_freebusy_rejects_21_calendars(fake):
    async with connect(fake) as c:
        r = await c.call_tool(
            "calendar_freebusy",
            {"time_min": "2026-11-04", "time_max": "2026-11-05", "calendar_ids": [f"c{i}" for i in range(21)]},
        )
    assert r.is_error
    assert fake.freebusy_calls == []


async def test_freebusy_without_calendar_ids_queries_the_primary_calendar(fake):
    async with connect(fake) as c:
        r = await c.call_tool("calendar_freebusy", {"time_min": "2026-11-04", "time_max": "2026-11-05"})
    assert not r.is_error
    assert fake.freebusy_calls[0]["calendar_ids"] == ["user@example.com"]


async def test_freebusy_rejects_an_empty_calendar_list(fake):
    async with connect(fake) as c:
        r = await c.call_tool(
            "calendar_freebusy", {"time_min": "2026-11-04", "time_max": "2026-11-05", "calendar_ids": []}
        )
    assert r.is_error
    assert fake.freebusy_calls == []
