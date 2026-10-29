"""Higher-level mailbox operations built on the client and the parsing models."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from google_personal_mcp.gmail.client import GmailClient
from google_personal_mcp.gmail.labels import REJECTED_LABELS, LabelCatalog
from google_personal_mcp.gmail.parsing import MessageSummary, sender_address
from google_personal_mcp.gmail.targets import NARROWING_SCAN_LIMIT, Target


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


class LabelChangeResult(BaseModel):
    """What gmail_modify_labels changed. Keys are label names; `count` is the number of distinct ids changed."""

    count: int
    added: dict[str, list[str]]
    removed: dict[str, list[str]]
    exact: bool


class TrashResult(BaseModel):
    """What gmail_trash or gmail_untrash changed; `inbox_ids` is the record needed to undo a trash exactly."""

    count: int
    message_ids: list[str]
    exact: bool
    inbox_ids: list[str]


def _resolve_labels(catalog: LabelCatalog, add: Sequence[str], remove: Sequence[str]) -> tuple[list[str], list[str]]:
    """Turn the requested names into de-duplicated ids, rejecting every invalid combination."""
    if not add and not remove:
        raise ValueError("Pass at least one label in add_labels or remove_labels.")
    resolved: list[list[str]] = []
    for names in (add, remove):
        ids: list[str] = []
        for name in names:
            label_id = catalog.id_for(name)
            if label_id.upper() in REJECTED_LABELS:
                if label_id.upper() == "TRASH":
                    raise ValueError(
                        f"Label '{name}' cannot be changed with gmail_modify_labels; use gmail_trash for the trash."
                    )
                raise ValueError(f"Label '{name}' cannot be changed.")
            if label_id not in ids:
                ids.append(label_id)
        resolved.append(ids)
    both = set(resolved[0]) & set(resolved[1])
    if both:
        raise ValueError(f"Label '{catalog.name_for(sorted(both)[0])}' is in both add_labels and remove_labels.")
    return resolved[0], resolved[1]


def modify_labels(
    client: GmailClient, catalog: LabelCatalog, target: Target, *, add: Sequence[str], remove: Sequence[str]
) -> LabelChangeResult:
    """Add and remove labels. In query mode only the messages that actually change are written and reported."""
    add_ids, remove_ids = _resolve_labels(catalog, add, remove)
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    exact = target.query is not None
    if target.message_ids is not None:
        ids = list(target.message_ids)
        added = {catalog.name_for(label_id): list(ids) for label_id in add_ids}
        removed = {catalog.name_for(label_id): list(ids) for label_id in remove_ids}
        changed = ids
    else:
        assert target.query is not None and target.max_messages is not None  # noqa: S101 - narrows the type
        base, _ = client.collect_message_ids(target.query, limit=target.max_messages)
        for label_ids, has_label, store in ((add_ids, False, added), (remove_ids, True, removed)):
            for label_id in label_ids:
                scan, truncated = client.collect_message_ids(
                    target.query, limit=NARROWING_SCAN_LIMIT, label_ids=[label_id]
                )
                exact = exact and not truncated
                holders = set(scan)
                store[catalog.name_for(label_id)] = [i for i in base if (i in holders) == has_label]
        touched = {i for group in (*added.values(), *removed.values()) for i in group}
        changed = [i for i in base if i in touched]
    if changed:
        client.batch_modify(changed, add_label_ids=add_ids, remove_label_ids=remove_ids)
    return LabelChangeResult(count=len(changed), added=added, removed=removed, exact=exact)


def trash_messages(client: GmailClient, target: Target) -> TrashResult:
    """Move the target to the trash and record which of the messages were in the inbox (trashing drops INBOX)."""
    if target.message_ids is not None:
        ids = list(target.message_ids)
        in_inbox = {
            str(item.get("id")) for item in client.get_messages_metadata(ids) if "INBOX" in item.get("labelIds", [])
        }
        inbox_ids = [i for i in ids if i in in_inbox]
        exact = False
    else:
        assert target.query is not None and target.max_messages is not None  # noqa: S101 - narrows the type
        ids, _ = client.collect_message_ids(target.query, limit=target.max_messages)
        exact = True
        inbox_ids = []
        if ids:
            scan, truncated = client.collect_message_ids(target.query, limit=NARROWING_SCAN_LIMIT, label_ids=["INBOX"])
            inbox = set(scan)
            inbox_ids = [i for i in ids if i in inbox]
            exact = not truncated
    if ids:
        client.trash(ids)
    return TrashResult(count=len(ids), message_ids=ids, exact=exact, inbox_ids=inbox_ids)


def untrash_messages(client: GmailClient, target: Target, *, restore_inbox: Sequence[str] = ()) -> TrashResult:
    """Restore the target from the trash; INBOX is re-added only to restored ids listed in `restore_inbox`."""
    if target.message_ids is not None:
        ids = list(target.message_ids)
        exact = False
    else:
        assert target.query is not None and target.max_messages is not None  # noqa: S101 - narrows the type
        ids, _ = client.collect_message_ids(
            f"in:trash ({target.query})", limit=target.max_messages, include_spam_trash=True
        )
        exact = True
    inbox_ids: list[str] = []
    if ids:
        client.untrash(ids)
        wanted = set(restore_inbox)
        inbox_ids = [i for i in ids if i in wanted]
        if inbox_ids:
            client.batch_modify(inbox_ids, add_label_ids=["INBOX"])
    return TrashResult(count=len(ids), message_ids=ids, exact=exact, inbox_ids=inbox_ids)
