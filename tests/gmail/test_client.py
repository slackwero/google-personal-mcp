import httplib2
import pytest
from googleapiclient.errors import HttpError

from google_personal_mcp.gmail.client import GmailApiError, GmailClient
from tests.fakes import FakeService, b64url, http_error


class RecordingLimiter:
    def __init__(self) -> None:
        self.acquired: list[int] = []

    def acquire(self, units: int) -> None:
        self.acquired.append(units)


def make_client(service: FakeService) -> tuple[GmailClient, RecordingLimiter]:
    limiter = RecordingLimiter()
    return GmailClient(service, limiter), limiter  # type: ignore[arg-type]


def test_metadata_is_batched_by_50_and_keeps_order():
    service = FakeService({"messages.get": lambda kw: {"id": kw["id"]}})
    client, _ = make_client(service)
    ids = [f"m{i}" for i in range(120)]
    result = client.get_messages_metadata(list(reversed(ids)))
    assert [m["id"] for m in result] == list(reversed(ids))
    assert [len(b) for b in service.batches] == [50, 50, 20]
    first = service.calls_to("messages.get")[0]
    assert first["format"] == "metadata"
    assert first["metadataHeaders"] == ["From", "To", "Subject", "Date"]


def test_metadata_charges_20_units_per_message():
    client, limiter = make_client(FakeService({"messages.get": lambda kw: {"id": kw["id"]}}))
    client.get_messages_metadata(["a", "b", "c"])
    assert limiter.acquired == [60]


def test_metadata_item_error_fails_the_whole_call():
    client, _ = make_client(FakeService(error=http_error(404, "Requested entity was not found.")))
    with pytest.raises(GmailApiError, match="404"):
        client.get_messages_metadata(["a"])


def test_batch_modify_chunks_by_1000():
    service = FakeService()
    client, limiter = make_client(service)
    client.batch_modify([f"m{i}" for i in range(2500)], add_label_ids=["L1"], remove_label_ids=["INBOX"])
    bodies = [kw["body"] for kw in service.calls_to("messages.batchModify")]
    assert [len(b["ids"]) for b in bodies] == [1000, 1000, 500]
    assert bodies[0]["addLabelIds"] == ["L1"] and bodies[0]["removeLabelIds"] == ["INBOX"]
    assert limiter.acquired == [50, 50, 50]


def test_trash_adds_the_trash_label():
    service = FakeService()
    client, _ = make_client(service)
    client.trash(["a"])
    (body,) = (kw["body"] for kw in service.calls_to("messages.batchModify"))
    assert body == {"ids": ["a"], "addLabelIds": ["TRASH"], "removeLabelIds": []}


def _paged_list(total: int):
    def respond(kwargs: dict) -> dict:
        start = int(kwargs.get("pageToken", 0))
        end = min(start + kwargs["maxResults"], total)
        body: dict = {"messages": [{"id": f"m{i}"} for i in range(start, end)], "resultSizeEstimate": total}
        if end < total:
            body["nextPageToken"] = str(end)
        return body

    return respond


def test_collect_message_ids_stops_at_limit_and_reports_more():
    service = FakeService({"messages.list": _paged_list(1200)})
    client, _ = make_client(service)
    ids, more = client.collect_message_ids("q", limit=700)
    assert ids == [f"m{i}" for i in range(700)]
    assert more is True
    assert [kw["maxResults"] for kw in service.calls_to("messages.list")] == [500, 200]


def test_collect_message_ids_reports_no_more_when_exhausted():
    client, _ = make_client(FakeService({"messages.list": _paged_list(30)}))
    ids, more = client.collect_message_ids("q", limit=100)
    assert len(ids) == 30 and more is False


def test_list_message_ids_passes_filters():
    service = FakeService({"messages.list": _paged_list(2)})
    client, limiter = make_client(service)
    page = client.list_message_ids("from:x", max_results=900, include_spam_trash=True, label_ids=["INBOX"])
    kwargs = service.calls_to("messages.list")[0]
    assert kwargs["q"] == "from:x" and kwargs["maxResults"] == 500
    assert kwargs["includeSpamTrash"] is True and kwargs["labelIds"] == ["INBOX"]
    assert page.ids == ["m0", "m1"] and page.next_page_token is None and page.result_size_estimate == 2
    assert limiter.acquired == [5]


def test_http_404_becomes_readable_error():
    client = GmailClient(FakeService(error=http_error(404, "Requested entity was not found.")), RecordingLimiter())  # type: ignore[arg-type]
    with pytest.raises(GmailApiError, match="Gmail API error 404: Requested entity was not found") as exc:
        client.get_message_full("abc")
    assert exc.value.status == 404


def test_error_with_unparseable_body_does_not_leak_it():
    error = HttpError(resp=httplib2.Response({"status": 500}), content=b"<html>secret internals</html>")
    client, _ = make_client(FakeService(error=error))
    with pytest.raises(GmailApiError) as exc:
        client.get_profile_email()
    assert "secret" not in str(exc.value) and exc.value.status == 500


def test_execute_uses_retries():
    service = FakeService({"messages.get": {"id": "a"}, "getProfile": {"emailAddress": "user@example.com"}})
    client, _ = make_client(service)
    client.get_message_full("a")
    assert client.get_profile_email() == "user@example.com"
    assert service.executes and all(e["num_retries"] == 3 for e in service.executes)


def test_full_message_and_thread_costs():
    client, limiter = make_client(FakeService({"messages.get": {}, "threads.get": {}}))
    client.get_message_full("a")
    client.get_thread_full("t")
    assert limiter.acquired == [60, 120]


def test_list_labels_enriched_with_counts():
    service = FakeService(
        {
            "labels.list": {"labels": [{"id": "INBOX", "name": "INBOX"}]},
            "labels.get": {"id": "INBOX", "messagesTotal": 10, "messagesUnread": 3},
        }
    )
    client, limiter = make_client(service)
    assert client.list_labels() == [{"id": "INBOX", "name": "INBOX", "messagesTotal": 10, "messagesUnread": 3}]
    assert limiter.acquired == [1, 1]


def test_list_labels_without_counts_makes_one_request():
    service = FakeService({"labels.list": {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]}})
    client, limiter = make_client(service)
    assert client.list_labels(with_counts=False) == [{"id": "INBOX", "name": "INBOX", "type": "system"}]
    assert service.calls_to("labels.get") == []
    assert limiter.acquired == [1]


def test_create_label():
    service = FakeService({"labels.create": {"id": "Label_1", "name": "x"}})
    client, limiter = make_client(service)
    assert client.create_label("x")["id"] == "Label_1"
    assert service.calls_to("labels.create")[0]["body"] == {"name": "x"}
    assert limiter.acquired == [5]


@pytest.mark.parametrize("payload", [b"hello", b"\xff\xfe\xfd\xfc", b"ab"])
def test_attachment_data_is_base64url_decoded(payload):
    service = FakeService({"messages.attachments.get": {"data": b64url(payload)}})
    client, limiter = make_client(service)
    assert client.get_attachment_data("m", "a") == payload
    assert limiter.acquired == [20]
    assert service.calls_to("messages.attachments.get")[0] == {"userId": "me", "messageId": "m", "id": "a"}
