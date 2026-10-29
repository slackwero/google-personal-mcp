import pytest

from google_personal_mcp.gmail.client import GmailApiError
from google_personal_mcp.gmail.operations import modify_labels, top_senders, trash_messages, untrash_messages
from google_personal_mcp.gmail.parsing import MessageSummary
from google_personal_mcp.gmail.targets import NARROWING_SCAN_LIMIT, validate_target
from tests.gmail.helpers import catalog, catalog_with, mailbox


def msg(message_id: str, sender: str, size: int, date: str) -> MessageSummary:
    return MessageSummary(
        id=message_id, thread_id="t", date=date, sender=sender, subject="s", snippet="", size_bytes=size, label_ids=[]
    )


SUMMARIES = [
    msg("1", "Alice <A@example.com>", 1, "2024-02-01T00:00:00Z"),
    msg("2", "a@example.com", 2, "2024-01-01T00:00:00Z"),
    msg("3", "A <a@example.com>", 3, "2024-03-01T00:00:00Z"),
    msg("4", "b@example.com", 100, "2024-02-15T00:00:00Z"),
]


def test_top_senders_groups_by_address_and_sorts():
    by_count = top_senders(SUMMARIES, sort_by="count", limit=10)
    assert [(s.address, s.count) for s in by_count] == [("a@example.com", 3), ("b@example.com", 1)]
    a = by_count[0]
    assert (a.total_size_bytes, a.oldest_date, a.newest_date) == (6, "2024-01-01T00:00:00Z", "2024-03-01T00:00:00Z")
    by_size = top_senders(SUMMARIES, sort_by="size", limit=10)
    assert [s.address for s in by_size] == ["b@example.com", "a@example.com"]


def test_top_senders_limit_and_tie_break_by_address():
    tied = [msg("1", "z@example.com", 5, "2024-01-01T00:00:00Z"), msg("2", "m@example.com", 5, "2024-01-01T00:00:00Z")]
    assert [s.address for s in top_senders(tied, sort_by="count", limit=10)] == ["m@example.com", "z@example.com"]
    assert [s.address for s in top_senders(tied, sort_by="size", limit=1)] == ["m@example.com"]


def test_top_senders_ignores_empty_dates_for_range():
    rows = [msg("1", "a@example.com", 1, ""), msg("2", "a@example.com", 1, "2024-01-01T00:00:00Z")]
    (stats,) = top_senders(rows, sort_by="count", limit=5)
    assert stats.oldest_date == stats.newest_date == "2024-01-01T00:00:00Z"


def test_add_label_query_mode_excludes_already_labeled():
    fake = mailbox(m1=["INBOX"], m2=["INBOX", "Label_1"])
    r = modify_labels(fake, catalog_with("Label_1", "Keep"), validate_target(None, "q", 10), add=["Keep"], remove=[])
    assert r.added == {"Keep": ["m1"]} and r.count == 1 and r.exact is True
    assert fake.batch_modify_calls == [{"ids": ["m1"], "add": ["Label_1"], "remove": []}]


def test_mark_read_only_reports_unread():
    fake = mailbox(m1=["UNREAD"], m2=[], m3=["UNREAD", "INBOX"])
    r = modify_labels(fake, catalog(), validate_target(None, "q", 10), add=[], remove=["UNREAD"])
    assert r.removed == {"UNREAD": ["m1", "m3"]} and r.added == {} and r.count == 2
    assert fake.batch_modify_calls == [{"ids": ["m1", "m3"], "add": [], "remove": ["UNREAD"]}]
    assert fake.labels_by_id["m2"] == set()


def test_multiple_labels_tracked_separately():
    fake = mailbox(m1=["Label_1"], m2=[])
    cat = catalog_with("Label_1", "A", "Label_2", "B")
    r = modify_labels(fake, cat, validate_target(None, "q", 10), add=["A", "B"], remove=[])
    assert r.added == {"A": ["m2"], "B": ["m1", "m2"]} and r.count == 2
    assert fake.labels_by_id == {"m1": {"Label_1", "Label_2"}, "m2": {"Label_1", "Label_2"}}


def test_add_and_remove_together_count_distinct_ids():
    fake = mailbox(m1=["UNREAD"], m2=["INBOX", "UNREAD"], m3=[])
    r = modify_labels(fake, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=["UNREAD"])
    assert r.added == {"STARRED": ["m1", "m2", "m3"]} and r.removed == {"UNREAD": ["m1", "m2"]} and r.count == 3


def test_zero_matches_skips_batch_modify():
    empty_mailbox = mailbox()
    r = modify_labels(empty_mailbox, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=[])
    assert r.count == 0 and empty_mailbox.batch_modify_calls == []


def test_everything_already_in_target_state_skips_batch_modify():
    fake = mailbox(m1=["STARRED"])
    r = modify_labels(fake, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=["UNREAD"])
    assert r.count == 0 and r.added == {"STARRED": []} and fake.batch_modify_calls == []


def test_id_mode_is_not_exact():
    fake = mailbox(m1=["STARRED"], m2=[])
    r = modify_labels(fake, catalog(), validate_target(["m1", "m2"], None, None), add=["STARRED"], remove=["UNREAD"])
    assert r.exact is False and r.count == 2
    assert r.added == {"STARRED": ["m1", "m2"]} and r.removed == {"UNREAD": ["m1", "m2"]}
    assert [c["limit"] for c in fake.list_calls] == []


def test_truncated_narrowing_scan_marks_inexact(monkeypatch):
    fake = mailbox(m1=[], m2=[])
    real = fake.collect_message_ids

    def truncating(query, *, limit, include_spam_trash=False, label_ids=()):
        ids, more = real(query, limit=limit, include_spam_trash=include_spam_trash, label_ids=label_ids)
        return ids, more or (limit == NARROWING_SCAN_LIMIT)

    monkeypatch.setattr(fake, "collect_message_ids", truncating)
    r = modify_labels(fake, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=[])
    assert r.exact is False and r.count == 2


@pytest.mark.parametrize(
    ("add", "remove", "message"),
    [
        ([], [], "at least one"),
        (["STARRED"], ["starred"], "both"),
        (["TRASH"], [], "use gmail_trash"),
        ([], ["spam"], "cannot be changed"),
        (["Nope"], [], "Unknown label"),
    ],
)
def test_invalid_label_requests_raise_before_any_write(add, remove, message):
    fake = mailbox(m1=[])
    with pytest.raises(ValueError, match=message):
        modify_labels(fake, catalog(), validate_target(None, "q", 10), add=add, remove=remove)
    assert fake.batch_modify_calls == []


def test_trash_query_mode_excludes_trashed_and_returns_ids():
    fake = mailbox(m1=["Label_1"], m2=["TRASH"], m3=[])
    r = trash_messages(fake, validate_target(None, "q", 10))
    assert r.message_ids == ["m1", "m3"] and r.count == 2 and r.exact is True
    assert fake.list_calls[0]["include_spam_trash"] is False and fake.list_calls[0]["limit"] == 10
    assert fake.trash_calls == [["m1", "m3"]]


def test_trash_with_no_matches_makes_no_write():
    fake = mailbox()
    r = trash_messages(fake, validate_target(None, "q", 10))
    assert r.count == 0 and fake.trash_calls == [] and fake.batch_modify_calls == []


def test_untrash_query_mode_is_restricted_to_trash():
    fake = mailbox(m1=["TRASH"], m2=["TRASH"])
    r = untrash_messages(fake, validate_target(None, "from:x", 10))
    assert r.message_ids == ["m1", "m2"] and r.exact is True and r.inbox_ids == []
    assert fake.list_calls[0]["query"] == "in:trash (from:x)" and fake.list_calls[0]["include_spam_trash"] is True


def test_untrash_with_no_matches_makes_no_write():
    fake = mailbox()
    r = untrash_messages(fake, validate_target(None, "q", 10), restore_inbox=["m1"])
    assert r.count == 0 and fake.batch_modify_calls == []


def test_trash_query_mode_records_inbox_ids():
    fake = mailbox(m1=["INBOX", "UNREAD"], m2=["Label_1"])
    r = trash_messages(fake, validate_target(None, "q", 10))
    assert r.message_ids == ["m1", "m2"] and r.inbox_ids == ["m1"] and r.exact is True
    assert fake.labels_by_id["m1"] == {"TRASH", "UNREAD"}  # the fake drops INBOX, like Gmail


def test_trash_id_mode_reads_inbox_ids():
    fake = mailbox(m1=["INBOX"], m2=[], m3=["INBOX"])
    r = trash_messages(fake, validate_target(["m3", "m2", "m1"], None, None))
    assert r.message_ids == ["m3", "m2", "m1"] and r.inbox_ids == ["m3", "m1"] and r.exact is False
    assert fake.list_calls == []


def test_trash_id_mode_metadata_error_means_no_write():
    fake = mailbox(m1=["INBOX"])
    fake.errors["m1"] = GmailApiError(404, "Requested entity was not found.")
    with pytest.raises(GmailApiError):
        trash_messages(fake, validate_target(["m1"], None, None))
    assert fake.batch_modify_calls == []


def test_trash_truncated_inbox_scan_marks_inexact(monkeypatch):
    fake = mailbox(m1=["INBOX"])
    real = fake.collect_message_ids

    def truncating(query, *, limit, include_spam_trash=False, label_ids=()):
        ids, more = real(query, limit=limit, include_spam_trash=include_spam_trash, label_ids=label_ids)
        return ids, more or (limit == NARROWING_SCAN_LIMIT)

    monkeypatch.setattr(fake, "collect_message_ids", truncating)
    r = trash_messages(fake, validate_target(None, "q", 10))
    assert r.exact is False and r.message_ids == ["m1"]


def test_untrash_restores_inbox_only_for_restored_ids():
    fake = mailbox(m1=["TRASH"], m2=["TRASH"], m3=["Label_1"])
    r = untrash_messages(fake, validate_target(["m1", "m2"], None, None), restore_inbox=["m1", "m3"])
    assert r.inbox_ids == ["m1"]
    assert fake.labels_by_id == {"m1": {"INBOX"}, "m2": set(), "m3": {"Label_1"}}
