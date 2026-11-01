import pytest
from mcp.client import Client
from mcp.server import MCPServer

from google_personal_mcp.gcalendar.tools import register_calendar_tools
from tests.fakes import FakeCalendarClient

pytestmark = pytest.mark.anyio

WRITE_TOOLS = {"calendar_create_event", "calendar_update_event", "calendar_delete_event"}


def own_event(event_id: str = "e1") -> dict:
    return {
        "id": event_id,
        "summary": "Dentist",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-04T10:00:00-03:00", "timeZone": "America/Santiago"},
        "end": {"dateTime": "2026-11-04T11:00:00-03:00", "timeZone": "America/Santiago"},
        "organizer": {"email": "user@example.com", "self": True},
    }


@pytest.fixture
def fake() -> FakeCalendarClient:
    return FakeCalendarClient()


def connect(fake: FakeCalendarClient) -> Client:
    server = MCPServer("test")
    register_calendar_tools(server, lambda: fake)
    return Client(server)


async def list_write_tools(fake):
    async with connect(fake) as c:
        return {t.name: t for t in (await c.list_tools()).tools if t.name in WRITE_TOOLS}


async def test_write_tool_annotations(fake):
    tools = await list_write_tools(fake)
    assert set(tools) == WRITE_TOOLS
    for tool in tools.values():
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.open_world_hint is False
    assert tools["calendar_create_event"].annotations.destructive_hint is False
    assert tools["calendar_update_event"].annotations.destructive_hint is False
    assert tools["calendar_delete_event"].annotations.destructive_hint is True


async def test_write_descriptions_promise_no_notifications(fake):
    tools = await list_write_tools(fake)
    for tool in tools.values():
        assert "sends no invitations or notifications" in tool.description
        assert "other guests are read-only" in tool.description
    assert "exclusive" in tools["calendar_create_event"].description
    update = tools["calendar_update_event"].description
    assert "before" in update and "series: true" in update
    assert "30 days" in tools["calendar_delete_event"].description


async def test_create_event_through_the_tool(fake):
    async with connect(fake) as c:
        r = await c.call_tool(
            "calendar_create_event", {"summary": "Lunch", "start": "2026-11-05T13:00", "end": "2026-11-05T14:00"}
        )
    assert not r.is_error
    assert r.structured_content["summary"] == "Lunch"
    assert fake.insert_calls[0]["body"]["start"]["timeZone"] == "America/Santiago"


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("calendar_create_event", {"summary": "x", "start": "tomorrow"}, "Invalid time 'tomorrow'"),
        (
            "calendar_create_event",
            {"summary": "x", "start": "2026-11-05", "time_zone": "Mars/Base"},
            "Unknown time zone 'Mars/Base'",
        ),
        (
            "calendar_create_event",
            {"summary": "x", "start": "2026-11-05", "recurrence": ["FREQ=DAILY"]},
            "must start with",
        ),
        ("calendar_create_event", {"summary": "x", "start": "2026-11-05T09:00"}, "end is required"),
        (
            "calendar_create_event",
            {"summary": "x", "start": "2026-11-05T10:00", "end": "2026-11-05T09:00"},
            "end must be after the start",
        ),
        (
            "calendar_create_event",
            {"summary": "x", "start": "2026-11-05", "end": "2026-11-05T09:00"},
            "do not mix them",
        ),
        ("calendar_update_event", {"event_id": "e1"}, "at least one field"),
        ("calendar_update_event", {"event_id": "e1", "time_zone": "Europe/Madrid"}, "give start or end"),
        ("calendar_update_event", {"event_id": "e1", "start": "2026-11-05T10:00", "end": "bad"}, "Invalid time 'bad'"),
        ("calendar_update_event", {"event_id": "e1", "recurrence": ["EXDATE:1\nX"]}, "single line"),
    ],
)
async def test_invalid_input_fails_before_any_call(tool, args, message):
    touched: list[str] = []

    class Exploding:
        def __getattr__(self, name):
            touched.append(name)
            raise AssertionError(f"client used: {name}")

    def provider():
        touched.append("client_provider")
        return Exploding()

    server = MCPServer("test")
    register_calendar_tools(server, provider)
    async with Client(server) as c:
        r = await c.call_tool(tool, args)
    assert r.is_error
    assert touched == []
    assert message in r.content[0].text


async def test_update_result_round_trips(fake):
    fake.add_event("primary", own_event())
    async with connect(fake) as c:
        r = await c.call_tool("calendar_update_event", {"event_id": "e1", "start": "2026-11-04T15:00"})
        assert not r.is_error
        before = r.structured_content["before"]
        assert r.structured_content["event"]["start"].startswith("2026-11-04T15:00")
        r2 = await c.call_tool("calendar_update_event", {"event_id": "e1", **before})
        assert not r2.is_error
    event = fake.events["user@example.com"]["e1"]
    assert event["start"]["dateTime"] == "2026-11-04T10:00:00-03:00"
    assert event["end"]["dateTime"] == "2026-11-04T11:00:00-03:00"


async def test_delete_event_through_the_tool(fake):
    fake.add_event("primary", own_event())
    async with connect(fake) as c:
        r = await c.call_tool("calendar_delete_event", {"event_id": "e1"})
    assert not r.is_error
    assert r.structured_content["id"] == "e1"
    assert fake.delete_calls
