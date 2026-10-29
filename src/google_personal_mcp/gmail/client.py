"""Thin client over the Gmail API: quota accounting, retries, batching and readable errors."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_personal_mcp.gmail.parsing import decode_base64url
from google_personal_mcp.quota import QuotaLimiter

USER = "me"
RETRIES = 3
LIST_PAGE_MAX = 500
METADATA_BATCH_SIZE = 50
BATCH_MODIFY_CHUNK = 1000
METADATA_HEADERS = ["From", "To", "Subject", "Date"]

COST_PROFILE = 1
COST_LIST = 5
COST_MESSAGE_METADATA = 20
COST_MESSAGE_FULL = 60
COST_THREAD_FULL = 120
COST_LABEL_READ = 1
COST_LABEL_CREATE = 5
COST_BATCH_MODIFY = 50
COST_ATTACHMENT = 20


class GmailApiError(Exception):
    """A Gmail API call failed. The message is readable and never includes the raw response body."""

    def __init__(self, status: int | None, reason: str) -> None:
        super().__init__(f"Gmail API error {status}: {reason}")
        self.status = status


@dataclass(frozen=True)
class MessagePage:
    """One page of message ids from `messages.list`."""

    ids: list[str]
    next_page_token: str | None
    result_size_estimate: int


def _reason(error: HttpError) -> str:
    try:
        message = json.loads(error.content)["error"]["message"]
    except ValueError, KeyError, TypeError:
        return "unknown error"
    return message if isinstance(message, str) and message else "unknown error"


def _to_api_error(error: HttpError) -> GmailApiError:
    return GmailApiError(getattr(error.resp, "status", None), _reason(error))


def _chunks[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class GmailClient:
    """Gmail operations used by the MCP tools."""

    def __init__(self, service: Any, limiter: QuotaLimiter) -> None:  # noqa: ANN401 - googleapiclient resource
        self._service = service
        self._limiter = limiter

    @classmethod
    def from_credentials(cls, credentials: Any, limiter: QuotaLimiter) -> GmailClient:  # noqa: ANN401
        """Build a client from OAuth credentials."""
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(service, limiter)

    @property
    def _users(self) -> Any:  # noqa: ANN401
        return self._service.users()

    def _call(self, cost: int, request: Any) -> Any:  # noqa: ANN401
        self._limiter.acquire(cost)
        try:
            return request.execute(num_retries=RETRIES)
        except HttpError as error:
            raise _to_api_error(error) from error

    def get_profile_email(self) -> str:
        """Return the email address of the authenticated account."""
        return self._call(COST_PROFILE, self._users.getProfile(userId=USER))["emailAddress"]

    def list_message_ids(
        self,
        query: str,
        *,
        max_results: int,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        label_ids: Sequence[str] = (),
    ) -> MessagePage:
        """Return one page (at most 500) of message ids matching the query."""
        params: dict[str, Any] = {
            "userId": USER,
            "q": query,
            "maxResults": min(max_results, LIST_PAGE_MAX),
            "includeSpamTrash": include_spam_trash,
        }
        if page_token:
            params["pageToken"] = page_token
        if label_ids:
            params["labelIds"] = list(label_ids)
        response = self._call(COST_LIST, self._users.messages().list(**params))
        return MessagePage(
            ids=[m["id"] for m in response.get("messages", [])],
            next_page_token=response.get("nextPageToken"),
            result_size_estimate=response.get("resultSizeEstimate", 0),
        )

    def collect_message_ids(
        self,
        query: str,
        *,
        limit: int,
        include_spam_trash: bool = False,
        label_ids: Sequence[str] = (),
    ) -> tuple[list[str], bool]:
        """Page through results up to `limit` ids; the bool is True when more matches existed."""
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < limit:
            page = self.list_message_ids(
                query,
                max_results=min(LIST_PAGE_MAX, limit - len(ids)),
                page_token=page_token,
                include_spam_trash=include_spam_trash,
                label_ids=label_ids,
            )
            ids.extend(page.ids)
            page_token = page.next_page_token
            if page_token is None:
                break
        return ids[:limit], page_token is not None or len(ids) > limit

    def get_messages_metadata(self, ids: Sequence[str]) -> list[dict]:
        """Fetch headers metadata for many messages with HTTP batches; results keep the input order."""
        results: dict[str, dict] = {}
        for chunk in _chunks(ids, METADATA_BATCH_SIZE):
            results.update(self._metadata_batch(chunk))
        return [results[message_id] for message_id in ids]

    def _metadata_batch(self, chunk: Sequence[str]) -> dict[str, dict]:
        found: dict[str, dict] = {}
        failures: list[GmailApiError] = []

        def on_response(request_id: str, response: dict, exception: Exception | None) -> None:
            if isinstance(exception, HttpError):
                failures.append(_to_api_error(exception))
            elif exception is not None:
                failures.append(GmailApiError(None, "batch request failed"))
            else:
                found[request_id] = response

        self._limiter.acquire(COST_MESSAGE_METADATA * len(chunk))
        batch = self._service.new_batch_http_request(callback=on_response)
        for message_id in chunk:
            request = self._users.messages().get(
                userId=USER, id=message_id, format="metadata", metadataHeaders=METADATA_HEADERS
            )
            batch.add(request, request_id=message_id)
        try:
            batch.execute()
        except HttpError as error:
            raise _to_api_error(error) from error
        if failures:
            raise failures[0]
        return found

    def get_message_full(self, message_id: str) -> dict:
        """Fetch one message with its full payload."""
        return self._call(COST_MESSAGE_FULL, self._users.messages().get(userId=USER, id=message_id, format="full"))

    def get_thread_full(self, thread_id: str) -> dict:
        """Fetch one thread with the full payload of every message."""
        return self._call(COST_THREAD_FULL, self._users.threads().get(userId=USER, id=thread_id, format="full"))

    def list_labels(self, *, with_counts: bool = True) -> list[dict]:
        """List labels; with `with_counts` each is enriched with `messagesTotal` and `messagesUnread` (1 call each)."""
        labels = self._call(COST_LABEL_READ, self._users.labels().list(userId=USER)).get("labels", [])
        if not with_counts:
            return labels
        enriched = []
        for label in labels:
            detail = self._call(COST_LABEL_READ, self._users.labels().get(userId=USER, id=label["id"]))
            enriched.append(
                {
                    **label,
                    "messagesTotal": detail.get("messagesTotal", 0),
                    "messagesUnread": detail.get("messagesUnread", 0),
                }
            )
        return enriched

    def create_label(self, name: str) -> dict:
        """Create a user label."""
        return self._call(COST_LABEL_CREATE, self._users.labels().create(userId=USER, body={"name": name}))

    def batch_modify(
        self,
        ids: Sequence[str],
        *,
        add_label_ids: Sequence[str] = (),
        remove_label_ids: Sequence[str] = (),
    ) -> None:
        """Add and remove labels on many messages, in chunks of 1,000."""
        for chunk in _chunks(ids, BATCH_MODIFY_CHUNK):
            body = {"ids": list(chunk), "addLabelIds": list(add_label_ids), "removeLabelIds": list(remove_label_ids)}
            self._call(COST_BATCH_MODIFY, self._users.messages().batchModify(userId=USER, body=body))

    def trash(self, ids: Sequence[str]) -> None:
        """Move messages to the trash."""
        self.batch_modify(ids, add_label_ids=["TRASH"])

    def untrash(self, ids: Sequence[str]) -> None:
        """Restore messages from the trash."""
        self.batch_modify(ids, remove_label_ids=["TRASH"])

    def get_attachment_data(self, message_id: str, attachment_id: str) -> bytes:
        """Return the decoded bytes of an attachment."""
        response = self._call(
            COST_ATTACHMENT,
            self._users.messages().attachments().get(userId=USER, messageId=message_id, id=attachment_id),
        )
        data: str = response["data"]
        return decode_base64url(data)


type ClientProvider = Callable[[], GmailClient]
