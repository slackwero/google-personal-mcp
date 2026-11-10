"""Pieces shared by the Gmail and Calendar tool modules: the read-only annotation and the tool runner."""

from collections.abc import Callable

import anyio.to_thread
import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import Error as ApiClientError
from googleapiclient.errors import HttpError
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from google_personal_mcp.api_errors import GoogleApiError
from google_personal_mcp.auth import EXPIRED_TOKEN_MESSAGE, AuthError
from google_personal_mcp.config import ConfigError

# open_world_hint: every tool talks to Google, and the content it reads was written by anyone who can email or
# invite the account, so the domain is not closed.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Failures to reach Google that escape the client: the token refresh, the socket, httplib2 and googleapiclient itself.
NETWORK_ERRORS = (TransportError, OSError, httplib2.HttpLib2Error, ApiClientError)


def _network_error_message(error: Exception) -> str:
    """A short, readable message; an HttpError's text carries the response body, so only its status is kept."""
    detail = f"HTTP {error.status_code}" if isinstance(error, HttpError) else str(error) or "no details"
    return f"Could not reach Google: {type(error).__name__}: {detail}"


async def run_client[C, T](client_provider: Callable[[], C], call: Callable[[C], T]) -> T:
    """Run `call` with the client in a worker thread and turn expected failures into tool errors.

    The provider runs in the same thread because it reads the token file and may refresh it (blocking I/O). The
    client can also refresh the token on its own, so its RefreshError gets the same message as an expired token.
    """

    def work() -> T:
        return call(client_provider())

    try:
        return await anyio.to_thread.run_sync(work)
    except (ConfigError, AuthError, GoogleApiError, ValueError) as error:
        raise ToolError(str(error)) from error
    except RefreshError as error:
        raise ToolError(EXPIRED_TOKEN_MESSAGE) from error
    except NETWORK_ERRORS as error:
        raise ToolError(_network_error_message(error)) from error
