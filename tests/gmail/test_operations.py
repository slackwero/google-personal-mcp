import json
import logging

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
    assert fake.list_calls == []


def _truncating_narrowing_scans(fake, monkeypatch) -> None:
    """Make every narrowing scan (limit NARROWING_SCAN_LIMIT) report that more mail matched."""
    real = fake.collect_message_ids

    def truncating(query, *, limit, include_spam_trash=False, label_ids=()):
        ids, more = real(query, limit=limit, include_spam_trash=include_spam_trash, label_ids=label_ids)
        return ids, more or (limit == NARROWING_SCAN_LIMIT)

    monkeypatch.setattr(fake, "collect_message_ids", truncating)


def test_truncated_narrowing_scan_keeps_exact(monkeypatch):
    # The base is the newest max_messages matches, so every base holder of the label is within the first
    # max_messages results of the narrowing scan; a longer scan result does not make the outcome inexact.
    fake = mailbox(m1=["STARRED"], m2=[])
    _truncating_narrowing_scans(fake, monkeypatch)
    r = modify_labels(fake, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=[])
    assert r.exact is True and r.added == {"STARRED": ["m2"]} and r.count == 1
    assert [call["limit"] for call in fake.list_calls] == [10, NARROWING_SCAN_LIMIT]


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
    call = fake.list_calls[0]
    assert (call["query"], call["include_spam_trash"], call["limit"]) == ("(q) -in:trash", False, 10)
    assert fake.trash_calls == [["m1", "m3"]]


def test_trash_with_no_matches_makes_no_write():
    fake = mailbox()
    r = trash_messages(fake, validate_target(None, "q", 10))
    assert r.count == 0 and fake.trash_calls == [] and fake.batch_modify_calls == []


def test_untrash_query_mode_is_restricted_to_trash():
    fake = mailbox(m1=["TRASH"], m2=["TRASH"], m3=["INBOX"])
    r = untrash_messages(fake, validate_target(None, "from:x", 10))
    assert r.message_ids == ["m1", "m2"] and r.exact is True and r.inbox_ids == []
    call = fake.list_calls[0]
    assert (call["query"], call["include_spam_trash"], call["label_ids"]) == ("in:trash (from:x)", True, ["TRASH"])
    assert fake.labels_by_id["m3"] == {"INBOX"}


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
    assert r.message_ids == ["m3", "m2", "m1"] and r.inbox_ids == ["m3", "m1"] and r.exact is True
    assert r.more_matching is False and fake.list_calls == []


def test_trash_id_mode_skips_messages_already_in_the_trash():
    fake = mailbox(m1=["INBOX"], m2=["TRASH", "UNREAD"], m3=[])
    r = trash_messages(fake, validate_target(["m1", "m2", "m3"], None, None))
    assert (r.count, r.message_ids, r.inbox_ids, r.exact) == (2, ["m1", "m3"], ["m1"], True)
    assert fake.batch_modify_calls == [{"ids": ["m1", "m3"], "add": ["TRASH"], "remove": []}]
    assert fake.labels_by_id["m2"] == {"TRASH", "UNREAD"}


def test_trash_id_mode_with_everything_already_trashed_makes_no_write():
    fake = mailbox(m1=["TRASH"])
    r = trash_messages(fake, validate_target(["m1"], None, None))
    assert (r.count, r.message_ids, r.exact) == (0, [], True)
    assert fake.batch_modify_calls == []


def test_trash_id_mode_metadata_error_means_no_write():
    fake = mailbox(m1=["INBOX"])
    fake.errors["m1"] = GmailApiError(404, "Requested entity was not found.")
    with pytest.raises(GmailApiError):
        trash_messages(fake, validate_target(["m1"], None, None))
    assert fake.batch_modify_calls == []


def test_trash_truncated_inbox_scan_keeps_exact(monkeypatch):
    fake = mailbox(m1=["INBOX"], m2=[])
    _truncating_narrowing_scans(fake, monkeypatch)
    r = trash_messages(fake, validate_target(None, "q", 10))
    assert r.exact is True and r.message_ids == ["m1", "m2"] and r.inbox_ids == ["m1"]
    assert [call["limit"] for call in fake.list_calls] == [10, NARROWING_SCAN_LIMIT]


def test_untrash_restores_inbox_only_for_restored_ids():
    fake = mailbox(m1=["TRASH"], m2=["TRASH"], m3=["Label_1"])
    r = untrash_messages(fake, validate_target(["m1", "m2"], None, None), restore_inbox=["m1", "m3"])
    assert r.inbox_ids == ["m1"]
    assert fake.labels_by_id == {"m1": {"INBOX"}, "m2": set(), "m3": {"Label_1"}}


def test_untrash_writes_final_state_in_one_call_per_group():
    fake = mailbox(m1=["TRASH", "UNREAD"], m2=["TRASH"])
    untrash_messages(fake, validate_target(["m1", "m2"], None, None), restore_inbox=["m1"])
    assert fake.batch_modify_calls == [
        {"ids": ["m1"], "add": ["INBOX"], "remove": ["TRASH"]},
        {"ids": ["m2"], "add": [], "remove": ["TRASH"]},
    ]


def test_untrash_failure_leaves_no_half_restored_message():
    fake = mailbox(m1=["TRASH"])
    fake.method_errors["batch_modify"] = GmailApiError(500, "boom")
    with pytest.raises(GmailApiError):
        untrash_messages(fake, validate_target(["m1"], None, None), restore_inbox=["m1"])
    assert fake.labels_by_id["m1"] == {"TRASH"}


def test_max_messages_caps_modify_labels():
    fake = mailbox(m1=[], m2=[], m3=[])
    r = modify_labels(fake, catalog(), validate_target(None, "q", 2), add=["STARRED"], remove=[])
    assert fake.list_calls[0]["limit"] == 2
    assert r.added == {"STARRED": ["m1", "m2"]} and r.count == 2
    assert fake.batch_modify_calls == [{"ids": ["m1", "m2"], "add": ["STARRED"], "remove": []}]
    assert fake.labels_by_id == {"m1": {"STARRED"}, "m2": {"STARRED"}, "m3": set()}


def test_max_messages_caps_trash():
    fake = mailbox(m1=["INBOX"], m2=["INBOX"], m3=["INBOX"])
    r = trash_messages(fake, validate_target(None, "q", 2))
    assert fake.list_calls[0]["limit"] == 2
    assert r.message_ids == ["m1", "m2"] and r.inbox_ids == ["m1", "m2"]
    assert fake.batch_modify_calls == [{"ids": ["m1", "m2"], "add": ["TRASH"], "remove": []}]
    assert fake.labels_by_id == {"m1": {"TRASH"}, "m2": {"TRASH"}, "m3": {"INBOX"}}


def test_max_messages_caps_untrash():
    fake = mailbox(m1=["TRASH"], m2=["TRASH"], m3=["TRASH"])
    r = untrash_messages(fake, validate_target(None, "q", 2))
    assert fake.list_calls[0]["limit"] == 2
    assert r.message_ids == ["m1", "m2"]
    assert fake.batch_modify_calls == [{"ids": ["m1", "m2"], "add": [], "remove": ["TRASH"]}]
    assert fake.labels_by_id == {"m1": set(), "m2": set(), "m3": {"TRASH"}}


def test_query_mode_ids_are_deduplicated(monkeypatch):
    fake = mailbox(m1=[], m2=[])
    monkeypatch.setattr(fake, "collect_message_ids", lambda *a, **k: (["m1", "m2", "m1"], False))
    r = trash_messages(fake, validate_target(None, "q", 5))
    assert r.message_ids == ["m1", "m2"] and fake.trash_calls == [["m1", "m2"]]
    r = modify_labels(fake, catalog(), validate_target(None, "q", 5), add=[], remove=["UNREAD"])
    assert r.removed == {"UNREAD": ["m1", "m2"]} and fake.batch_modify_calls[-1]["ids"] == ["m1", "m2"]


def test_empty_base_skips_narrowing_scans():
    fake = mailbox()
    modify_labels(fake, catalog(), validate_target(None, "q", 5), add=["STARRED"], remove=["UNREAD"])
    assert len(fake.list_calls) == 1


@pytest.mark.parametrize(("cap", "more"), [(2, True), (3, False)])
def test_query_mode_reports_more_matching_when_the_cap_truncates(cap, more):
    target = validate_target(None, "q", cap)
    assert untrash_messages(mailbox(m1=["TRASH"], m2=["TRASH"], m3=["TRASH"]), target).more_matching is more
    assert trash_messages(mailbox(m1=["INBOX"], m2=["INBOX"], m3=["INBOX"]), target).more_matching is more
    r = modify_labels(mailbox(m1=[], m2=[], m3=[]), catalog(), target, add=["STARRED"], remove=[])
    assert r.more_matching is more


def test_id_mode_never_reports_more_matching():
    fake = mailbox(m1=["INBOX"], m2=["TRASH"])
    ids = validate_target(["m1", "m2"], None, None)
    assert modify_labels(fake, catalog(), ids, add=["STARRED"], remove=[]).more_matching is False
    assert untrash_messages(fake, validate_target(["m2"], None, None)).more_matching is False
    assert trash_messages(fake, validate_target(["m1"], None, None)).more_matching is False


@pytest.mark.parametrize("label", ["INBOX", "UNREAD"])
def test_single_label_removal_makes_progress_on_repeated_calls(label):
    fake = mailbox(**{f"m{i}": [label] for i in range(1, 6)})
    target = validate_target(None, "category:promotions", 2)
    results = [modify_labels(fake, catalog(), target, add=[], remove=[label]) for _ in range(3)]
    assert [(r.count, r.more_matching) for r in results] == [(2, True), (2, True), (1, False)]
    assert all(r.exact for r in results)
    assert [r.removed[label] for r in results] == [["m1", "m2"], ["m3", "m4"], ["m5"]]
    assert all(label not in labels for labels in fake.labels_by_id.values())
    assert [(call["label_ids"], call["limit"]) for call in fake.list_calls] == [([label], 2)] * 3


def test_removal_with_additions_still_lists_the_whole_query():
    fake = mailbox(m1=["INBOX"], m2=[])
    r = modify_labels(fake, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=["INBOX"])
    assert r.removed == {"INBOX": ["m1"]} and r.added == {"STARRED": ["m1", "m2"]}
    assert fake.list_calls[0]["label_ids"] == []


class Cancelled(Exception):
    """Stands in for the cancellation that anyio.from_thread.check_cancelled raises."""


def cancel() -> None:
    raise Cancelled


WRITES = {
    "modify_labels_query": lambda fake, **hook: modify_labels(
        fake, catalog(), validate_target(None, "q", 10), add=["STARRED"], remove=[], **hook
    ),
    "modify_labels_single_removal": lambda fake, **hook: modify_labels(
        fake, catalog(), validate_target(None, "q", 10), add=[], remove=["INBOX"], **hook
    ),
    "modify_labels_ids": lambda fake, **hook: modify_labels(
        fake, catalog(), validate_target(["m1", "m2"], None, None), add=["STARRED"], remove=[], **hook
    ),
    "trash_query": lambda fake, **hook: trash_messages(fake, validate_target(None, "q", 10), **hook),
    "trash_ids": lambda fake, **hook: trash_messages(fake, validate_target(["m1", "m2"], None, None), **hook),
    "untrash_query": lambda fake, **hook: untrash_messages(
        fake, validate_target(None, "q", 10), restore_inbox=["m3"], **hook
    ),
    "untrash_ids": lambda fake, **hook: untrash_messages(
        fake, validate_target(["m3", "m4"], None, None), restore_inbox=["m3"], **hook
    ),
}


def write_mailbox():
    return mailbox(m1=["INBOX"], m2=["INBOX"], m3=["TRASH"], m4=["TRASH"])


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_cancelled_before_write_hook_prevents_every_write(name, caplog):
    fake = write_mailbox()
    with caplog.at_level(logging.INFO), pytest.raises(Cancelled):
        WRITES[name](fake, before_write=cancel)
    assert fake.batch_modify_calls == []
    assert caplog.records == []


@pytest.mark.parametrize(("name", "writes"), [(name, 2 if name.startswith("untrash") else 1) for name in WRITES])
def test_before_write_runs_immediately_before_each_write(name, writes):
    fake = write_mailbox()
    writes_done_at_check: list[int] = []
    WRITES[name](fake, before_write=lambda: writes_done_at_check.append(len(fake.batch_modify_calls)))
    assert writes_done_at_check == list(range(writes))
    assert len(fake.batch_modify_calls) == writes


def test_cancellation_between_untrash_writes_stops_the_second_one():
    fake = mailbox(m1=["TRASH"], m2=["TRASH"])
    checks: list[int] = []

    def cancel_second_write() -> None:
        checks.append(1)
        if len(checks) == 2:
            raise Cancelled

    with pytest.raises(Cancelled):
        untrash_messages(
            fake, validate_target(["m1", "m2"], None, None), restore_inbox=["m1"], before_write=cancel_second_write
        )
    assert fake.labels_by_id == {"m1": {"INBOX"}, "m2": {"TRASH"}}


def _logged(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "google_personal_mcp.gmail.operations"]


def test_trash_logs_the_changed_ids_and_inbox_ids_only(caplog):
    fake = mailbox(m1=["INBOX"], m2=[], m3=["TRASH"])
    with caplog.at_level(logging.INFO):
        trash_messages(fake, validate_target(None, "from:boss@example.com subject:private", 10))
    assert _logged(caplog) == ['gmail_trash wrote 2 message(s): ids=["m1", "m2"] inbox_ids=["m1"]']
    assert caplog.records[0].levelno == logging.INFO
    assert "boss" not in caplog.text and "private" not in caplog.text


def test_untrash_logs_each_write(caplog):
    fake = mailbox(m1=["TRASH"], m2=["TRASH"])
    with caplog.at_level(logging.INFO):
        untrash_messages(fake, validate_target(["m1", "m2"], None, None), restore_inbox=["m1"])
    assert _logged(caplog) == [
        'gmail_untrash wrote 1 message(s): ids=["m1"] inbox_restored=true',
        'gmail_untrash wrote 1 message(s): ids=["m2"] inbox_restored=false',
    ]


def test_modify_labels_logs_the_changed_ids_per_label_id(caplog):
    fake = mailbox(m1=["INBOX", "Label_1"], m2=["INBOX"])
    with caplog.at_level(logging.INFO):
        modify_labels(
            fake,
            catalog_with("Label_1", "Keep"),
            validate_target(None, "label:secret", 10),
            add=["Keep"],
            remove=["INBOX"],
        )
    (line,) = _logged(caplog)
    prefix = 'gmail_modify_labels wrote 2 message(s): ids=["m1", "m2"] '
    assert line == prefix + 'added={"Label_1": ["m2"]} removed={"INBOX": ["m1", "m2"]}'
    assert "secret" not in caplog.text and "Keep" not in caplog.text


def test_log_lines_escape_hostile_ids(caplog):
    fake = mailbox()
    hostile = "m1\nINFO forged line"
    fake.add_message(hostile, ["INBOX"])
    with caplog.at_level(logging.INFO):
        trash_messages(fake, validate_target([hostile], None, None))
    (line,) = _logged(caplog)
    assert "\n" not in line and json.dumps(hostile) in line


def test_no_log_without_a_successful_write(caplog):
    with caplog.at_level(logging.INFO):
        trash_messages(mailbox(), validate_target(None, "q", 10))
        failing = mailbox(m1=["INBOX"])
        failing.method_errors["batch_modify"] = GmailApiError(500, "boom")
        with pytest.raises(GmailApiError):
            trash_messages(failing, validate_target(None, "q", 10))
    assert _logged(caplog) == []
