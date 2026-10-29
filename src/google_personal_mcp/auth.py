"""OAuth login, token storage and the account check."""

import contextlib
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport import Request
from google.auth.transport.requests import Request as RequestsRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from google_personal_mcp.config import Settings, require_account

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
MODULE_SCOPES: Mapping[str, tuple[str, ...]] = {"gmail": (GMAIL_MODIFY_SCOPE,)}
LOGIN_HINT = (
    "Run `uv run google-personal-mcp login` from this server's repo folder, with GOOGLE_PERSONAL_MCP_ACCOUNT set "
    "(see the README)."
)
EXPIRED_TOKEN_MESSAGE = (
    "The Google token expired or was revoked. While the OAuth app is in Testing, "
    f"Google expires refresh tokens after 7 days. {LOGIN_HINT}"
)

type EmailFetcher = Callable[[Credentials], str]


class AuthError(Exception):
    """Authentication failed; the message tells the user how to fix it and ends with LOGIN_HINT."""


def scopes_for(modules: Iterable[str]) -> list[str]:
    """Return the sorted, unique OAuth scopes needed by the enabled modules."""
    return sorted({scope for module in modules for scope in MODULE_SCOPES[module]})


def save_token(credentials: Credentials, path: Path) -> None:
    """Write the token atomically, readable only by the current user."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        tmp_path = Path(tmp.name)
        try:
            os.chmod(tmp_path, 0o600)
            tmp.write(credentials.to_json())
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
    try:
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _read_token(path: Path) -> Credentials:
    if not path.exists():
        raise AuthError(f"Not logged in: no token found. {LOGIN_HINT}")
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise ValueError("token file is not a JSON object")
        return Credentials.from_authorized_user_info(info)
    except (ValueError, OSError) as exc:  # json.JSONDecodeError is a ValueError
        raise AuthError(f"The saved token is unreadable or corrupted. {LOGIN_HINT}") from exc


def load_credentials(settings: Settings, *, request: Request | None = None) -> Credentials:
    """Load the saved token, refreshing and re-saving it when it has expired."""
    credentials = _read_token(settings.token_path)
    required = scopes_for(settings.modules)
    if not credentials.has_scopes(required):
        raise AuthError(f"The saved token lacks a required scope ({', '.join(required)}). {LOGIN_HINT}")
    if credentials.valid:
        return credentials
    try:
        credentials.refresh(request or RequestsRequest())
    except RefreshError as exc:
        raise AuthError(EXPIRED_TOKEN_MESSAGE) from exc
    except TransportError as exc:
        raise AuthError(f"Could not reach Google to refresh the token; check the connection. {LOGIN_HINT}") from exc
    try:
        save_token(credentials, settings.token_path)
    except OSError as exc:
        raise AuthError(
            f"The token was refreshed but could not be saved to {settings.token_path} ({exc.strerror or exc}); "
            f"check that the folder is writable. {LOGIN_HINT}"
        ) from exc
    return credentials


class AccountGuard:
    """Checks that a token belongs to the expected account, caching the token's address per refresh token.

    The address is fetched again only when the refresh token changes (for example after a re-login), so a mismatch
    never outlives a re-login. One guard can be shared by several threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._refresh_token: str | None = None
        self._address: str | None = None

    def check(self, credentials: Credentials, *, expected: str, fetch_email: Callable[[], str]) -> None:
        """Raise AuthError unless `credentials` belong to `expected`; `fetch_email` runs only on a cache miss."""
        with self._lock:
            if self._address is None or credentials.refresh_token != self._refresh_token:
                self._address = fetch_email().lower()
                self._refresh_token = credentials.refresh_token
            address = self._address
        if address != expected.lower():
            raise AuthError(
                f"The token belongs to {address}, but this server is configured for {expected.lower()}. {LOGIN_HINT}"
            )


def run_login(
    settings: Settings,
    *,
    fetch_email: EmailFetcher,
    flow_factory: Callable[[Path, list[str]], InstalledAppFlow] = InstalledAppFlow.from_client_secrets_file,
) -> str:
    """Run the browser OAuth flow and save the token only if it belongs to the configured account."""
    account = require_account(settings)
    if not settings.client_secret_path.is_file():
        raise AuthError(f"client_secret.json not found at {settings.client_secret_path}. {LOGIN_HINT}")
    flow = flow_factory(settings.client_secret_path, scopes_for(settings.modules))
    credentials = flow.run_local_server(port=0, open_browser=True, login_hint=account)
    email = fetch_email(credentials).lower()
    if email != account:
        raise AuthError(f"Signed in as {email}, but this server is configured for {account}. {LOGIN_HINT}")
    save_token(credentials, settings.token_path)
    return email
