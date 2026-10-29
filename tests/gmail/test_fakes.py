import inspect

from google_personal_mcp.gmail.client import GmailApiError, GmailClient
from tests.fakes import FakeGmailClient


def _public(cls: type) -> dict[str, inspect.Signature]:
    return {n: inspect.signature(f) for n, f in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def test_fake_mirrors_the_real_client_surface():
    real, fake = _public(GmailClient), _public(FakeGmailClient)
    for name, signature in real.items():
        if name == "from_credentials":
            continue
        assert name in fake, name
        assert list(fake[name].parameters) == list(signature.parameters), name


def test_filters_by_labels_and_spam_trash_and_records_calls():
    fake = FakeGmailClient()
    fake.add_message("a", ["INBOX"])
    fake.add_message("b", ["INBOX", "TRASH"])
    fake.add_message("c", ["STARRED"])
    page = fake.list_message_ids("anything", max_results=10, label_ids=["INBOX"])
    assert page.ids == ["a"]
    assert fake.list_message_ids("q", max_results=10, include_spam_trash=True, label_ids=["INBOX"]).ids == ["a", "b"]
    ids, more = fake.collect_message_ids("q", limit=1, include_spam_trash=True)
    assert ids == ["a"] and more is True
    assert fake.list_calls[0]["query"] == "anything" and fake.list_calls[2]["limit"] == 1


def test_mutations_round_trip_and_scripted_errors():
    fake = FakeGmailClient()
    fake.add_message("a", ["INBOX"])
    fake.batch_modify(["a"], add_label_ids=["X"], remove_label_ids=["INBOX"])
    assert fake.labels_by_id["a"] == {"X"}
    fake.trash(["a"])
    assert "TRASH" in fake.labels_by_id["a"]
    fake.untrash(["a"])
    assert "TRASH" not in fake.labels_by_id["a"]
    fake.errors["a"] = GmailApiError(404, "Requested entity was not found.")
    try:
        fake.get_messages_metadata(["a"])
    except GmailApiError as error:
        assert error.status == 404
    else:
        raise AssertionError("expected GmailApiError")


def test_fake_trash_drops_inbox_and_records_batch_modify():
    fake = FakeGmailClient()
    fake.add_message("a", ["INBOX", "UNREAD"])
    fake.trash(["a"])
    assert fake.labels_by_id["a"] == {"TRASH", "UNREAD"}
    assert fake.batch_modify_calls == [{"ids": ["a"], "add": ["TRASH"], "remove": []}]
    fake.untrash(["a"])
    assert fake.labels_by_id["a"] == {"UNREAD"}
    assert fake.batch_modify_calls[-1] == {"ids": ["a"], "add": [], "remove": ["TRASH"]}
    assert fake.trash_calls == [["a"]] and fake.untrash_calls == [["a"]]


def test_fake_method_error_hook():
    fake = FakeGmailClient()
    fake.method_errors["create_label"] = GmailApiError(409, "Label name exists or conflicts")
    try:
        fake.create_label("x")
    except GmailApiError as error:
        assert error.status == 409
    else:
        raise AssertionError("expected GmailApiError")
    assert fake.label_list == []
