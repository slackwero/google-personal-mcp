"""Thin client over the Google Calendar API: quota accounting, retries and readable errors."""

from collections.abc import Callable, Sequence
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_personal_mcp.api_errors import GoogleApiError, http_error_reason
from google_personal_mcp.quota import QuotaLimiter

CALENDAR_REQUESTS_PER_MINUTE = 500
CALENDAR_BURST = 100
RETRIES = 3
PAGE_MAX = 250
SEND_UPDATES = "none"
REQUEST_COST = 1


class CalendarApiError(GoogleApiError):
    """A Calendar API call failed. The message is readable and never includes the raw response body."""

    API_NAME = "Calendar"


def _to_api_error(error: HttpError) -> CalendarApiError:
    return CalendarApiError(getattr(error.resp, "status", None), http_error_reason(error))


def _refuse_attendees(body: dict) -> None:
    if "attendees" in body:
        raise ValueError("Events with attendees are not supported: attendees are never sent")


class CalendarClient:
    """Calendar operations used by the MCP tools."""

    def __init__(
        self,
        service: Any,  # noqa: ANN401 - googleapiclient resource
        limiter: QuotaLimiter,
        *,
        account: str | None = None,
    ) -> None:
        self._service = service
        self._limiter = limiter
        self._account = account

    @classmethod
    def from_credentials(
        cls,
        credentials: Any,  # noqa: ANN401
        limiter: QuotaLimiter,
        *,
        account: str | None = None,
    ) -> CalendarClient:
        """Build a client from OAuth credentials."""
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return cls(service, limiter, account=account)

    @property
    def account(self) -> str | None:
        """The configured account this client acts for, which the write guards compare against `dataOwner`."""
        return self._account

    def _call(self, request: Any) -> Any:  # noqa: ANN401
        self._limiter.acquire(REQUEST_COST)
        try:
            return request.execute(num_retries=RETRIES)
        except HttpError as error:
            raise _to_api_error(error) from error

    def get_primary_calendar_id(self) -> str:
        """Return the id of the authenticated account's primary calendar."""
        return self._call(self._service.calendarList().get(calendarId="primary"))["id"]

    def list_calendars(self) -> list[dict]:
        """List every calendar in the account's calendar list, across all pages."""
        calendars: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": PAGE_MAX}
            if page_token:
                params["pageToken"] = page_token
            response = self._call(self._service.calendarList().list(**params))
            calendars.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return calendars

    def get_calendar_entry(self, calendar_id: str) -> dict:
        """Return one calendar-list entry (access role, time zone, summary)."""
        return self._call(self._service.calendarList().get(calendarId=calendar_id))

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
        """Return one raw page of events in the window, with recurring events expanded and ordered by start."""
        params: dict[str, Any] = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        return self._call(self._service.events().list(**params))

    def list_series_exceptions(self, calendar_id: str, ical_uid: str) -> list[dict]:
        """List every live event sharing the series' iCalUID: the series itself and its occurrences edited alone."""
        items: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "calendarId": calendar_id,
                "iCalUID": ical_uid,
                "singleEvents": False,
                "showDeleted": False,
                "maxResults": PAGE_MAX,
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._call(self._service.events().list(**params))
            items.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def get_event(self, calendar_id: str, event_id: str) -> dict:
        """Fetch one event."""
        return self._call(self._service.events().get(calendarId=calendar_id, eventId=event_id))

    def insert_event(self, calendar_id: str, body: dict) -> dict:
        """Create an event. Attendees are refused and no notification is ever sent."""
        _refuse_attendees(body)
        request = self._service.events().insert(calendarId=calendar_id, body=body, sendUpdates=SEND_UPDATES)
        return self._call(request)

    def patch_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
        """Update the given fields of an event. Attendees are refused and no notification is ever sent."""
        _refuse_attendees(body)
        request = self._service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=body, sendUpdates=SEND_UPDATES
        )
        return self._call(request)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event without notifying anyone."""
        self._call(self._service.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates=SEND_UPDATES))

    def query_freebusy(
        self,
        *,
        time_min: str,
        time_max: str,
        calendar_ids: Sequence[str],
        time_zone: str,
    ) -> dict:
        """Return the busy intervals of the given calendars in the window."""
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": time_zone,
            "items": [{"id": calendar_id} for calendar_id in calendar_ids],
        }
        return self._call(self._service.freebusy().query(body=body))


type CalendarClientProvider = Callable[[], CalendarClient]
