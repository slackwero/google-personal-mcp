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
    """What gmail_modify_labels changed. Keys are label names; `count` is the number of distinct ids changed.

    `more_matching` is True when the query matched more than `max_messages` (always False in id mode).
    """

    count: int
    added: dict[str, list[str]]
    removed: dict[str, list[str]]
    exact: bool
    more_matching: bool


class TrashResult(BaseModel):
    """What gmail_trash or gmail_untrash changed; `inbox_ids` is the record needed to undo a trash exactly.

    `more_matching` is True when the query matched more than `max_messages` (always False in id mode).
    """

    count: int
    message_ids: list[str]
    exact: bool
    more_matching: bool
    inbox_ids: list[str]


def _query_of(target: Target) -> tuple[str, int]:
    """Return the query and cap of a query-mode target."""
    if target.query is None or target.max_messages is None:
        raise ValueError("Expected a query target.")
    return target.query, target.max_messages


def _unique(ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _holders(client: GmailClient, query: str, label_id: str) -> set[str]:
    """Return the ids among the newest NARROWING_SCAN_LIMIT matches of `query` that carry `label_id`.

    This is exact for a base made of the newest `max_messages` (at most 1,000) matches of the same query. Gmail lists
    newest first, so every base message with the label is within the first `max_messages` results of the query
    restricted to the label. The scan limit leaves a margin of more than 4,000 for mail that arrives between the two
    listings, so a scan that stops at the limit does not make the result inexact.
    """
    scan, _more = client.collect_message_ids(query, limit=NARROWING_SCAN_LIMIT, label_ids=[label_id])
    return set(scan)


def check_label_request(add: Sequence[str], remove: Sequence[str]) -> None:
    """Reject, without needing the label catalog, an empty request or a name in both lists."""
    if not add and not remove:
        raise ValueError("Pass at least one label in add_labels or remove_labels.")
    overlap = {name.casefold() for name in add} & {name.casefold() for name in remove}
    if overlap:
        raise ValueError(f"Label '{sorted(overlap)[0]}' is in both add_labels and remove_labels.")


def _resolve_labels(catalog: LabelCatalog, add: Sequence[str], remove: Sequence[str]) -> tuple[list[str], list[str]]:
    """Turn the requested names into de-duplicated ids, rejecting every invalid combination."""
    check_label_request(add, remove)
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


def _narrow_by_scans(
    client: GmailClient, catalog: LabelCatalog, query: str, base: list[str], add_ids: list[str], remove_ids: list[str]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Split the base, per label, into the messages that lack an added label or carry a removed one."""
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    for label_ids, has_label, store in ((add_ids, False, added), (remove_ids, True, removed)):
        for label_id in label_ids:
            holders = _holders(client, query, label_id) if base else set()
            store[catalog.name_for(label_id)] = [i for i in base if (i in holders) == has_label]
    return added, removed


def modify_labels(
    client: GmailClient, catalog: LabelCatalog, target: Target, *, add: Sequence[str], remove: Sequence[str]
) -> LabelChangeResult:
    """Add and remove labels. In query mode only the messages that actually change are written and reported."""
    add_ids, remove_ids = _resolve_labels(catalog, add, remove)
    more_matching = False
    if target.message_ids is not None:
        changed = list(target.message_ids)
        added = {catalog.name_for(label_id): list(changed) for label_id in add_ids}
        removed = {catalog.name_for(label_id): list(changed) for label_id in remove_ids}
    elif not add_ids and len(remove_ids) == 1:
        # Removing one label (archive, mark read): list only the messages that carry it, so the cap counts messages
        # that will change and repeated calls make progress.
        query, cap = _query_of(target)
        listed, more_matching = client.collect_message_ids(query, limit=cap, label_ids=remove_ids)
        changed = _unique(listed)
        added, removed = {}, {catalog.name_for(remove_ids[0]): list(changed)}
    else:
        query, cap = _query_of(target)
        listed, more_matching = client.collect_message_ids(query, limit=cap)
        base = _unique(listed)
        added, removed = _narrow_by_scans(client, catalog, query, base, add_ids, remove_ids)
        touched = {i for group in (*added.values(), *removed.values()) for i in group}
        changed = [i for i in base if i in touched]
    if changed:
        client.batch_modify(changed, add_label_ids=add_ids, remove_label_ids=remove_ids)
    return LabelChangeResult(
        count=len(changed),
        added=added,
        removed=removed,
        exact=target.query is not None,
        more_matching=more_matching,
    )


def trash_messages(client: GmailClient, target: Target) -> TrashResult:
    """Move the target to the trash and record which of the messages were in the inbox (trashing drops INBOX).

    Messages already in the trash are skipped in both modes: they are neither written nor reported.
    """
    more_matching = False
    if target.message_ids is not None:
        requested = list(target.message_ids)
        metadata = client.get_messages_metadata(requested)
        labels = {
            message_id: set(item.get("labelIds", [])) for message_id, item in zip(requested, metadata, strict=True)
        }
        ids = [i for i in requested if "TRASH" not in labels[i]]
        inbox = {i for i in ids if "INBOX" in labels[i]}
    else:
        query, cap = _query_of(target)
        listed, more_matching = client.collect_message_ids(f"({query}) -in:trash", limit=cap)
        ids = _unique(listed)
        inbox = _holders(client, query, "INBOX") if ids else set()
    inbox_ids = [i for i in ids if i in inbox]
    if ids:
        client.trash(ids)
    return TrashResult(count=len(ids), message_ids=ids, exact=True, more_matching=more_matching, inbox_ids=inbox_ids)


def untrash_messages(client: GmailClient, target: Target, *, restore_inbox: Sequence[str] = ()) -> TrashResult:
    """Restore the target from the trash; INBOX is added only to restored ids listed in `restore_inbox`.

    Each message gets its final labels in a single write, so a failure cannot leave a restored message archived.
    """
    more_matching = False
    if target.message_ids is not None:
        ids = list(target.message_ids)
        exact = False
    else:
        query, cap = _query_of(target)
        listed, more_matching = client.collect_message_ids(
            f"in:trash ({query})", limit=cap, include_spam_trash=True, label_ids=["TRASH"]
        )
        ids = _unique(listed)
        exact = True
    wanted = set(restore_inbox)
    inbox_ids = [i for i in ids if i in wanted]
    others = [i for i in ids if i not in wanted]
    if inbox_ids:
        client.batch_modify(inbox_ids, add_label_ids=["INBOX"], remove_label_ids=["TRASH"])
    if others:
        client.batch_modify(others, remove_label_ids=["TRASH"])
    return TrashResult(count=len(ids), message_ids=ids, exact=exact, more_matching=more_matching, inbox_ids=inbox_ids)
