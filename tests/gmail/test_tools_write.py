import pytest
from mcp.client import Client

from google_personal_mcp.gmail.client import GmailApiError
from google_personal_mcp.server import build_server
from tests.gmail.helpers import label_list, mailbox
from tests.gmail.test_tools_read import settings

pytestmark = pytest.mark.anyio

WRITE_TOOLS = {"gmail_create_label", "gmail_modify_labels", "gmail_trash", "gmail_untrash"}


def connect(fake) -> Client:
    fake.label_list = label_list(("Label_1", "Keep"))
    return Client(build_server(settings(), client_provider=lambda: fake))


@pytest.mark.parametrize("label", ["TRASH", "spam", "SENT", "DRAFT"])
async def test_modify_labels_rejects_system_labels(label):
    fake = mailbox(m1=["INBOX"])
    async with connect(fake) as client:
        result = await client.call_tool("gmail_modify_labels", {"message_ids": ["m1"], "add_labels": [label]})
    assert result.is_error
    assert fake.batch_modify_calls == [] and fake.list_calls == []


async def test_modify_labels_rejects_unknown_label():
    fake = mailbox(m1=["INBOX"])
    async with connect(fake) as client:
        result = await client.call_tool("gmail_modify_labels", {"message_ids": ["m1"], "add_labels": ["Nope"]})
    assert result.is_error and "Unknown label: 'Nope'" in result.content[0].text
    assert fake.batch_modify_calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"message_ids": ["m1"]},
        {"message_ids": ["m1"], "add_labels": ["Keep"], "remove_labels": ["Keep"]},
        {"query": "q", "add_labels": ["Keep"]},
        {"query": "", "max_messages": 5, "add_labels": ["Keep"]},
        {"add_labels": ["Keep"]},
    ],
)
async def test_modify_labels_input_errors_make_no_write(arguments):
    fake = mailbox(m1=["INBOX"])
    async with connect(fake) as client:
        result = await client.call_tool("gmail_modify_labels", arguments)
    assert result.is_error
    assert fake.batch_modify_calls == []


async def test_modify_labels_by_name_in_query_mode():
    fake = mailbox(m1=["INBOX"], m2=["INBOX", "Label_1"])
    async with connect(fake) as client:
        result = await client.call_tool(
            "gmail_modify_labels",
            {"query": "q", "max_messages": 10, "add_labels": ["Keep"], "remove_labels": ["INBOX"]},
        )
    assert not result.is_error
    assert result.structured_content == {
        "count": 2,
        "added": {"Keep": ["m1"]},
        "removed": {"INBOX": ["m1", "m2"]},
        "exact": True,
    }
    assert fake.labels_by_id == {"m1": {"Label_1"}, "m2": {"Label_1"}}


async def test_trash_requires_cap_in_query_mode():
    fake = mailbox(m1=["INBOX"])
    async with connect(fake) as client:
        result = await client.call_tool("gmail_trash", {"query": "q"})
    assert result.is_error and fake.batch_modify_calls == []


async def test_trash_rejects_ids_and_query_together():
    fake = mailbox(m1=["INBOX"])
    async with connect(fake) as client:
        result = await client.call_tool("gmail_trash", {"message_ids": ["m1"], "query": "q", "max_messages": 5})
    assert result.is_error and fake.batch_modify_calls == []


async def test_write_tool_annotations():
    async with connect(mailbox()) as client:
        tools = {tool.name: tool.annotations for tool in (await client.list_tools()).tools}
    expected = {
        "gmail_create_label": (False, False, False),
        "gmail_modify_labels": (False, False, True),
        "gmail_untrash": (False, False, True),
    }
    for name, (read_only, destructive, idempotent) in expected.items():
        hints = tools[name]
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint) == (
            read_only,
            destructive,
            idempotent,
            False,
        ), name
    trash = tools["gmail_trash"]
    assert (trash.read_only_hint, trash.destructive_hint, trash.open_world_hint) == (False, True, False)
    assert WRITE_TOOLS <= set(tools)


async def test_trash_tool_is_destructive():
    async with connect(mailbox()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert tools["gmail_trash"].annotations.destructive_hint is True
    assert tools["gmail_trash"].annotations.read_only_hint is False
    for name in WRITE_TOOLS - {"gmail_trash"}:
        assert tools[name].annotations.destructive_hint is False, name


async def test_descriptions_state_cap_and_undo():
    async with connect(mailbox()) as client:
        tools = {tool.name: tool.description for tool in (await client.list_tools()).tools}
    for name in ("gmail_modify_labels", "gmail_trash", "gmail_untrash"):
        assert "1000" in tools[name] and "max_messages" in tools[name], name
    for name in ("gmail_trash", "gmail_untrash"):
        assert "restore_inbox" in tools[name] and "stay archived" in tools[name], name


async def test_trash_then_untrash_round_trip():
    fake = mailbox(m1=["INBOX", "UNREAD"], m2=["Label_1"], m3=["TRASH"])
    original = {k: set(v) for k, v in fake.labels_by_id.items()}
    async with connect(fake) as client:
        trashed = await client.call_tool("gmail_trash", {"query": "q", "max_messages": 10})
        assert trashed.structured_content == {
            "count": 2,
            "message_ids": ["m1", "m2"],
            "exact": True,
            "inbox_ids": ["m1"],
        }
        restored = await client.call_tool(
            "gmail_untrash",
            {
                "message_ids": trashed.structured_content["message_ids"],
                "restore_inbox": trashed.structured_content["inbox_ids"],
            },
        )
    assert restored.structured_content["inbox_ids"] == ["m1"]
    assert fake.labels_by_id == original


async def test_untrash_without_restore_inbox_leaves_messages_archived():
    fake = mailbox(m1=["INBOX"])
    async with connect(fake) as client:
        await client.call_tool("gmail_trash", {"message_ids": ["m1"]})
        result = await client.call_tool("gmail_untrash", {"message_ids": ["m1"]})
    assert result.structured_content["inbox_ids"] == [] and fake.labels_by_id["m1"] == set()


async def test_create_label():
    fake = mailbox()
    async with connect(fake) as client:
        result = await client.call_tool("gmail_create_label", {"name": "New"})
    assert not result.is_error and result.structured_content["name"] == "New"


async def test_create_label_conflict_is_tool_error():
    fake = mailbox()
    fake.method_errors["create_label"] = GmailApiError(409, "Label name exists or conflicts")
    async with connect(fake) as client:
        result = await client.call_tool("gmail_create_label", {"name": "Keep"})
    assert result.is_error and "conflicts" in result.content[0].text
