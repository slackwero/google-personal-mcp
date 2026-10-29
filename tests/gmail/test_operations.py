from google_personal_mcp.gmail.operations import top_senders
from google_personal_mcp.gmail.parsing import MessageSummary


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
