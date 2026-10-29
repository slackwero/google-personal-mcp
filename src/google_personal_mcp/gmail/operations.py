"""Higher-level mailbox operations built on the client and the parsing models."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from google_personal_mcp.gmail.parsing import MessageSummary, sender_address


class SenderStats(BaseModel):
    """Aggregated view of the messages received from one address."""

    address: str
    count: int
    total_size_bytes: int
    oldest_date: str
    newest_date: str


def top_senders(
    summaries: Sequence[MessageSummary], *, sort_by: Literal["count", "size"], limit: int
) -> list[SenderStats]:
    """Group summaries by sender address and return the top `limit`, ties broken by address."""
    groups: dict[str, list[MessageSummary]] = {}
    for summary in summaries:
        groups.setdefault(sender_address(summary.sender), []).append(summary)
    stats = []
    for address, items in groups.items():
        dates = [item.date for item in items if item.date]
        stats.append(
            SenderStats(
                address=address,
                count=len(items),
                total_size_bytes=sum(item.size_bytes for item in items),
                oldest_date=min(dates, default=""),
                newest_date=max(dates, default=""),
            )
        )
    stats.sort(key=lambda s: (-(s.count if sort_by == "count" else s.total_size_bytes), s.address))
    return stats[:limit]
