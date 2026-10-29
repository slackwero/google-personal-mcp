import pytest
from mcp.client import Client

from google_personal_mcp.config import load_settings
from google_personal_mcp.gmail.client import GmailApiError
from google_personal_mcp.server import build_server
from tests.gmail.helpers import label_list, mailbox, settings

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
        "more_matching": False,
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
    assert [n for n in WRITE_TOOLS if tools[n].destructive_hint] == ["gmail_trash"]


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
            "more_matching": False,
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


async def test_repeated_archive_calls_report_more_matching_until_done():
    fake = mailbox(**{f"m{i}": ["INBOX"] for i in range(1, 6)})
    arguments = {"query": "category:promotions", "max_messages": 2, "remove_labels": ["INBOX"]}
    async with connect(fake) as client:
        results = [(await client.call_tool("gmail_modify_labels", arguments)).structured_content for _ in range(3)]
    assert [(r["count"], r["more_matching"]) for r in results] == [(2, True), (2, True), (1, False)]
    assert all("INBOX" not in labels for labels in fake.labels_by_id.values())


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


async def test_bad_input_makes_no_api_call():
    calls = []

    def provider():
        calls.append("provider")
        return mailbox()

    client = Client(build_server(settings(), client_provider=provider))
    async with client:
        for arguments in ({}, {"query": "q"}, {"message_ids": ["m1"], "query": "q", "max_messages": 1}):
            assert (await client.call_tool("gmail_untrash", arguments)).is_error
            assert (await client.call_tool("gmail_trash", arguments)).is_error
        assert (await client.call_tool("gmail_modify_labels", {"message_ids": ["m1"]})).is_error
        both = {"message_ids": ["m1"], "add_labels": ["X"], "remove_labels": ["x"]}
        assert (await client.call_tool("gmail_modify_labels", both)).is_error
    assert calls == []


def _part(part_id: str, filename: str, body: dict) -> dict:
    return {"partId": part_id, "filename": filename, "mimeType": "application/pdf", "body": body}


def _download_client(fake, tmp_path):
    fake.label_list = label_list()
    config = load_settings(
        {
            "HOME": "/nonexistent-home",
            "GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com",
            "GOOGLE_PERSONAL_MCP_DOWNLOAD_DIR": str(tmp_path / "dl"),
        }
    )
    return Client(build_server(config, client_provider=lambda: fake))


def _message(*parts: dict) -> dict:
    return {"id": "m1", "payload": {"partId": "", "mimeType": "multipart/mixed", "parts": list(parts)}}


async def test_download_tool_writes_file(tmp_path):
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("1", "../report.pdf", {"attachmentId": "att1", "size": 5}))
    fake.attachments[("m1", "att1")] = b"hello"
    async with _download_client(fake, tmp_path) as client:
        result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": "1"})
    assert not result.is_error
    saved = tmp_path / "dl" / "report.pdf"
    assert result.structured_content == {"path": str(saved), "size_bytes": 5, "filename": "report.pdf"}
    assert saved.read_bytes() == b"hello"


async def test_download_inline_body_data(tmp_path):
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("2", "", {"data": "aGk", "size": 2}))
    async with _download_client(fake, tmp_path) as client:
        result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": "2"})
    assert not result.is_error
    assert result.structured_content["filename"] == "attachment-m1-2"
    assert (tmp_path / "dl" / "attachment-m1-2").read_bytes() == b"hi"


async def test_download_unknown_part_is_tool_error(tmp_path):
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("1", "a.pdf", {"attachmentId": "att1"}))
    async with _download_client(fake, tmp_path) as client:
        for part_id in ("9", ""):
            result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": part_id})
            assert result.is_error
    assert not (tmp_path / "dl").exists()


async def test_download_part_without_data_is_tool_error(tmp_path):
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("1", "a.pdf", {"size": 3}))
    async with _download_client(fake, tmp_path) as client:
        result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": "1"})
    assert result.is_error
    assert not (tmp_path / "dl").exists()


async def test_download_os_error_is_tool_error(tmp_path):
    blocker = tmp_path / "dl"
    blocker.write_text("a file where the directory should be")
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("1", "a.pdf", {"attachmentId": "att1"}))
    fake.attachments[("m1", "att1")] = b"x"
    async with _download_client(fake, tmp_path) as client:
        result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": "1"})
    assert result.is_error and "Could not save the attachment" in result.content[0].text


async def test_download_tool_metadata(tmp_path):
    async with _download_client(mailbox(), tmp_path) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "gmail_download_attachment")
    assert tool.annotations.read_only_hint is False and tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False and tool.annotations.open_world_hint is False
    assert "untrusted" in tool.description and "never overwritten" in tool.description


@pytest.mark.parametrize("body", [{"attachmentId": "att1", "size": 5}, {"data": "", "size": 5}])
async def test_download_empty_data_with_declared_size_is_tool_error(tmp_path, body):
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("1", "a.pdf", body))
    fake.attachments[("m1", "att1")] = b""
    async with _download_client(fake, tmp_path) as client:
        result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": "1"})
    assert result.is_error and "empty or corrupt" in result.content[0].text
    assert not (tmp_path / "dl").exists()


async def test_download_corrupt_inline_data_is_tool_error(tmp_path):
    fake = mailbox(m1=["INBOX"])
    fake.full["m1"] = _message(_part("1", "a.pdf", {"data": "a!b$", "size": 3}))
    async with _download_client(fake, tmp_path) as client:
        result = await client.call_tool("gmail_download_attachment", {"message_id": "m1", "part_id": "1"})
    assert result.is_error
    assert not (tmp_path / "dl").exists()
