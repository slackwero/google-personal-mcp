"""Errors shared by the Google API clients."""

import json
from typing import ClassVar

from googleapiclient.errors import HttpError


class GoogleApiError(Exception):
    """A Google API call failed. The message is readable and never includes the raw response body."""

    API_NAME: ClassVar[str] = "Google"

    def __init__(self, status: int | None, reason: str) -> None:
        super().__init__(f"{self.API_NAME} API error {status}: {reason}")
        self.status = status


def http_error_reason(error: HttpError) -> str:
    """Return the API's error message, or "unknown error" when the response body has none."""
    try:
        message = json.loads(error.content)["error"]["message"]
    except ValueError, KeyError, TypeError:
        return "unknown error"
    return message if isinstance(message, str) and message else "unknown error"
