"""Test doubles: a fake googleapiclient service plus in-memory GmailClient and CalendarClient."""

import base64
import itertools
from collections.abc import Callable, Sequence
from typing import Any

import httplib2
from googleapiclient.errors import HttpError

from google_personal_mcp.gcalendar.client import CalendarApiError
from google_personal_mcp.gmail.client import LIST_PAGE_MAX, GmailApiError, MessagePage

_RESOURCES = {"users", "messages", "threads", "labels", "attachments"}


def http_error(status: int, message: str) -> HttpError:
    """Build the HttpError googleapiclient raises for an API failure."""
    content = b'{"error": {"message": "' + message.encode() + b'"}}'
    return HttpError(resp=httplib2.Response({"status": status}), content=content)


class FakeRequest:
    """What `service.users().messages().get(...)` returns: nothing happens until `execute`."""

    def __init__(self, service: FakeService, name: str, kwargs: dict[str, Any]) -> None:
        self.service, self.name, self.kwargs = service, name, kwargs

    def run(self) -> Any:  # noqa: ANN401
        if self.service.error is not None:
            raise self.service.error
        response = self.service.responses.get(self.name, {})
        return response(self.kwargs) if callable(response) else response

    def execute(self, **execute_kwargs: Any) -> Any:  # noqa: ANN401
        self.service.executes.append({"name": self.name, **execute_kwargs})
        return self.run()


class _Node:
    def __init__(self, service: FakeService, path: str) -> None:
        self._service, self._path = service, path

    def __getattr__(self, name: str) -> _Node:
        return _Node(self._service, f"{self._path}.{name}" if self._path else name)

    def __call__(self, **kwargs: Any) -> _Node | FakeRequest:  # noqa: ANN401
        leaf = self._path.rsplit(".", 1)[-1]
        if leaf in _RESOURCES:
            return _Node(self._service, "" if leaf == "users" else self._path)
        name = self._path
        self._service.calls.append((name, kwargs))
        return FakeRequest(self._service, name, kwargs)


class FakeBatch:
    """Emulates `service.new_batch_http_request(callback=...)`."""

    def __init__(self, service: FakeService, callback: Callable[..., None]) -> None:
        self.service, self.callback = service, callback
        self.requests: list[tuple[str, FakeRequest]] = []

    def add(self, request: FakeRequest, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        self.service.batches.append([request_id for request_id, _ in self.requests])
        for request_id, request in self.requests:
            try:
                response = request.run()
            except HttpError as error:
                self.callback(request_id, None, error)
            else:
                self.callback(request_id, response, None)


class FakeService:
    """Emulates the chained googleapiclient surface that GmailClient uses, recording every call.

    `responses` maps a dotted method name (`messages.get`, `messages.attachments.get`) to a response or a
    callable taking the request kwargs. `error` makes every request raise.
    """

    def __init__(self, responses: dict[str, Any] | None = None, error: HttpError | None = None) -> None:
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.executes: list[dict[str, Any]] = []
        self.batches: list[list[str]] = []

    def users(self) -> _Node:
        return _Node(self, "")

    def calendarList(self) -> _Node:  # noqa: N802 - mirrors the googleapiclient resource name
        return _Node(self, "calendarList")

    def events(self) -> _Node:
        return _Node(self, "events")

    def freebusy(self) -> _Node:
        return _Node(self, "freebusy")

    def new_batch_http_request(self, callback: Callable[..., None]) -> FakeBatch:
        return FakeBatch(self, callback)

    def calls_to(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for call, kwargs in self.calls if call == name]


class FakeGmailClient:
    """In-memory mailbox mirroring the public surface of GmailClient (Ruling R2).

    The query text is opaque: only `label_ids` and `include_spam_trash` filter. Every call is recorded.
    """

    def __init__(self, email: str = "user@example.com") -> None:
        self.email = email
        self.labels_by_id: dict[str, set[str]] = {}
        self.metadata: dict[str, dict] = {}
        self.full: dict[str, dict] = {}
        self.threads: dict[str, dict] = {}
        self.label_list: list[dict] = []
        self.attachments: dict[tuple[str, str], bytes] = {}
        self.errors: dict[str, GmailApiError] = {}
        self.method_errors: dict[str, Exception] = {}
        self.list_calls: list[dict[str, Any]] = []
        self.batch_modify_calls: list[dict[str, Any]] = []
        self.trash_calls: list[list[str]] = []

    def add_message(
        self,
        message_id: str,
        labels: Sequence[str] = (),
        *,
        metadata: dict | None = None,
        full: dict | None = None,
    ) -> None:
        self.labels_by_id[message_id] = set(labels)
        if metadata is not None:
            self.metadata[message_id] = metadata
        if full is not None:
            self.full[message_id] = full

    def _hook(self, method: str) -> None:
        """Raise the error scripted for `method` in `method_errors`, if any."""
        if method in self.method_errors:
            raise self.method_errors[method]

    def _check(self, *ids: str) -> None:
        for item in ids:
            if item in self.errors:
                raise self.errors[item]

    def _matching(self, label_ids: Sequence[str], include_spam_trash: bool) -> list[str]:
        return [
            message_id
            for message_id, labels in self.labels_by_id.items()
            if set(label_ids) <= labels and (include_spam_trash or not labels & {"TRASH", "SPAM"})
        ]

    def get_profile_email(self) -> str:
        self._hook("get_profile_email")
        return self.email

    def list_message_ids(
        self,
        query: str,
        *,
        max_results: int,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        label_ids: Sequence[str] = (),
    ) -> MessagePage:
        self._hook("list_message_ids")
        self.list_calls.append(
            {
                "query": query,
                "label_ids": list(label_ids),
                "include_spam_trash": include_spam_trash,
                "max_results": max_results,
                "limit": None,
                "page_token": page_token,
            }
        )
        matching = self._matching(label_ids, include_spam_trash)
        start = int(page_token) if page_token else 0
        end = start + min(max_results, LIST_PAGE_MAX)
        return MessagePage(matching[start:end], str(end) if end < len(matching) else None, len(matching))

    def collect_message_ids(
        self,
        query: str,
        *,
        limit: int,
        include_spam_trash: bool = False,
        label_ids: Sequence[str] = (),
    ) -> tuple[list[str], bool]:
        self._hook("collect_message_ids")
        self.list_calls.append(
            {
                "query": query,
                "label_ids": list(label_ids),
                "include_spam_trash": include_spam_trash,
                "max_results": None,
                "limit": limit,
                "page_token": None,
            }
        )
        matching = self._matching(label_ids, include_spam_trash)
        return matching[:limit], len(matching) > limit

    def get_messages_metadata(self, ids: Sequence[str]) -> list[dict]:
        self._hook("get_messages_metadata")
        self._check(*ids)
        return [self.metadata.get(i, {"id": i, "labelIds": sorted(self.labels_by_id.get(i, ()))}) for i in ids]

    def get_message_full(self, message_id: str) -> dict:
        self._hook("get_message_full")
        self._check(message_id)
        return self.full[message_id]

    def get_thread_full(self, thread_id: str) -> dict:
        self._hook("get_thread_full")
        self._check(thread_id)
        return self.threads[thread_id]

    def list_labels(self, *, with_counts: bool = True) -> list[dict]:
        self._hook("list_labels")
        return list(self.label_list)

    def create_label(self, name: str) -> dict:
        self._hook("create_label")
        label = {"id": f"Label_{len(self.label_list) + 1}", "name": name, "type": "user"}
        self.label_list.append(label)
        return label

    def batch_modify(
        self,
        ids: Sequence[str],
        *,
        add_label_ids: Sequence[str] = (),
        remove_label_ids: Sequence[str] = (),
    ) -> None:
        self._hook("batch_modify")
        self.batch_modify_calls.append({"ids": list(ids), "add": list(add_label_ids), "remove": list(remove_label_ids)})
        self._check(*ids)
        for message_id in ids:
            labels = self.labels_by_id.setdefault(message_id, set())
            labels |= set(add_label_ids)
            labels -= set(remove_label_ids)
            if "TRASH" in add_label_ids:
                labels.discard("INBOX")  # Gmail drops INBOX when a message is trashed (observed in Task 5)

    def trash(self, ids: Sequence[str]) -> None:
        self._hook("trash")
        self.trash_calls.append(list(ids))
        self.batch_modify(ids, add_label_ids=["TRASH"])

    def get_attachment_data(self, message_id: str, attachment_id: str) -> bytes:
        self._hook("get_attachment_data")
        self._check(message_id)
        return self.attachments[(message_id, attachment_id)]


class FakeCalendarClient:
    """In-memory calendars mirroring the public surface of CalendarClient (Ruling R2).

    `list_events` does not filter by time: it returns the calendar's events in insertion order. `"primary"` resolves
    to `primary_id` in every method. Every write is recorded.
    """

    def __init__(self, primary_id: str = "user@example.com") -> None:
        self.primary_id = primary_id
        self.account: str | None = primary_id
        self.entries: dict[str, dict] = {}
        self.events: dict[str, dict[str, dict]] = {}
        self.busy: dict[str, list[dict]] = {}
        self.method_errors: dict[str, Exception] = {}
        self.list_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.patch_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.freebusy_calls: list[dict[str, Any]] = []
        self.series_exception_calls: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.add_calendar(primary_id, primary=True)

    def add_calendar(
        self,
        calendar_id: str,
        *,
        access_role: str = "owner",
        time_zone: str = "America/Santiago",
        primary: bool = False,
        summary: str | None = None,
        data_owner: str | None = None,
    ) -> None:
        """Add a calendar-list entry. Google sets `dataOwner` only on secondary calendars."""
        entry = {"id": calendar_id, "summary": summary or calendar_id, "accessRole": access_role, "timeZone": time_zone}
        if primary:
            entry["primary"] = True
        if data_owner is not None:
            entry["dataOwner"] = data_owner
        self.entries[calendar_id] = entry
        self.events.setdefault(calendar_id, {})

    def add_event(self, calendar_id: str, event: dict) -> None:
        self.events[self._resolve(calendar_id)][event["id"]] = event

    def _hook(self, method: str) -> None:
        """Raise the error scripted for `method` in `method_errors`, if any."""
        if method in self.method_errors:
            raise self.method_errors[method]

    def _resolve(self, calendar_id: str) -> str:
        resolved = self.primary_id if calendar_id == "primary" else calendar_id
        if resolved not in self.entries:
            raise CalendarApiError(404, "Not Found")
        return resolved

    def _event(self, calendar_id: str, event_id: str) -> dict:
        events = self.events[self._resolve(calendar_id)]
        if event_id not in events:
            raise CalendarApiError(404, "Not Found")
        return events[event_id]

    def get_primary_calendar_id(self) -> str:
        self._hook("get_primary_calendar_id")
        return self.primary_id

    def list_calendars(self) -> list[dict]:
        self._hook("list_calendars")
        return list(self.entries.values())

    def get_calendar_entry(self, calendar_id: str) -> dict:
        self._hook("get_calendar_entry")
        return self.entries[self._resolve(calendar_id)]

    def list_events(
        self,
        calendar_id: str,
        *,
        time_min: str,
        time_max: str,
        max_results: int,
        query: str | None = None,
        page_token: str | None = None,
    ) -> dict:
        self._hook("list_events")
        resolved = self._resolve(calendar_id)
        self.list_calls.append(
            {
                "calendar_id": resolved,
                "time_min": time_min,
                "time_max": time_max,
                "max_results": max_results,
                "query": query,
                "page_token": page_token,
            }
        )
        return {"items": list(self.events[resolved].values()), "timeZone": self.entries[resolved]["timeZone"]}

    def list_series_exceptions(self, calendar_id: str, ical_uid: str) -> list[dict]:
        self._hook("list_series_exceptions")
        resolved = self._resolve(calendar_id)
        self.series_exception_calls.append({"calendar_id": resolved, "ical_uid": ical_uid})
        return [event for event in self.events[resolved].values() if event.get("iCalUID") == ical_uid]

    def get_event(self, calendar_id: str, event_id: str) -> dict:
        self._hook("get_event")
        return self._event(calendar_id, event_id)

    def insert_event(self, calendar_id: str, body: dict) -> dict:
        self._hook("insert_event")
        resolved = self._resolve(calendar_id)
        self.insert_calls.append({"calendar_id": resolved, "body": body})
        event = {"id": f"evt{next(self._ids)}", **body}
        if event["id"] in self.events[resolved]:
            raise CalendarApiError(409, "The requested identifier already exists.")
        self.events[resolved][event["id"]] = event
        return event

    def patch_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
        self._hook("patch_event")
        resolved = self._resolve(calendar_id)
        self.patch_calls.append({"calendar_id": resolved, "event_id": event_id, "body": body})
        event = self._event(resolved, event_id)
        event.update(body)
        return event

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._hook("delete_event")
        resolved = self._resolve(calendar_id)
        self.delete_calls.append({"calendar_id": resolved, "event_id": event_id})
        self._event(resolved, event_id)
        del self.events[resolved][event_id]

    def query_freebusy(
        self,
        *,
        time_min: str,
        time_max: str,
        calendar_ids: Sequence[str],
        time_zone: str,
    ) -> dict:
        self._hook("query_freebusy")
        resolved = [self._resolve(calendar_id) for calendar_id in calendar_ids]
        self.freebusy_calls.append(
            {"time_min": time_min, "time_max": time_max, "calendar_ids": resolved, "time_zone": time_zone}
        )
        return {"calendars": {c: {"busy": list(self.busy.get(c, []))} for c in resolved}}


def b64url(data: bytes) -> str:
    """Unpadded base64url, as the Gmail API returns attachment data."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
