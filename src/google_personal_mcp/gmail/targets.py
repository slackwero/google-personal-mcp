"""Bulk targets for the write tools: explicit message ids, or a query with a mandatory cap."""

from collections.abc import Sequence
from dataclasses import dataclass

MAX_TARGET = 1000
# How many messages a narrowing scan reads per label; a longer result makes the outcome inexact.
NARROWING_SCAN_LIMIT = 5000


@dataclass(frozen=True)
class Target:
    """Exactly one of `message_ids` or (`query` + `max_messages`)."""

    message_ids: tuple[str, ...] | None
    query: str | None
    max_messages: int | None


def validate_target(message_ids: Sequence[str] | None, query: str | None, max_messages: int | None) -> Target:
    """Validate the target inputs of a write tool and normalize them; raise ValueError when they are invalid."""
    if (message_ids is None) == (query is None):
        raise ValueError("Pass exactly one of message_ids or query.")
    if message_ids is not None:
        if max_messages is not None:
            raise ValueError("max_messages only applies together with query.")
        if not message_ids:
            raise ValueError("message_ids must not be empty.")
        if any(not item.strip() for item in message_ids):
            raise ValueError("message_ids must not contain empty ids.")
        unique = tuple(dict.fromkeys(message_ids))
        if len(unique) > MAX_TARGET:
            raise ValueError(f"message_ids accepts at most {MAX_TARGET} ids; got {len(unique)}.")
        return Target(message_ids=unique, query=None, max_messages=None)
    if query is None or not query.strip():
        raise ValueError("query must not be empty.")
    if max_messages is None:
        raise ValueError("max_messages is required with query.")
    if not 1 <= max_messages <= MAX_TARGET:
        raise ValueError(f"max_messages must be between 1 and {MAX_TARGET}.")
    return Target(message_ids=None, query=query, max_messages=max_messages)
