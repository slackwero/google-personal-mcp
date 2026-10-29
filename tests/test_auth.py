import errno
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials

from google_personal_mcp import auth
from google_personal_mcp.auth import (
    GMAIL_MODIFY_SCOPE,
    LOGIN_HINT,
    AccountGuard,
    AuthError,
    load_credentials,
    run_login,
    save_token,
    scopes_for,
)
from google_personal_mcp.config import ConfigError, Settings, load_settings

ACCOUNT = "user@example.com"


def make_creds(
    *,
    scopes: list[str] | None = None,
    expired: bool = False,
    refresh_token: str | None = "fake-refresh-token",  # noqa: S107
) -> Credentials:
    expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return Credentials(
        token="fake-access-token",  # noqa: S106
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",  # noqa: S106
        client_id="fake-client-id",
        client_secret="fake-client-secret",  # noqa: S106
        scopes=scopes if scopes is not None else [GMAIL_MODIFY_SCOPE],
        expiry=expiry,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings({"HOME": str(tmp_path), "GOOGLE_PERSONAL_MCP_ACCOUNT": ACCOUNT})


@pytest.fixture
def settings_without_account(tmp_path: Path) -> Settings:
    return load_settings({"HOME": str(tmp_path)})


class FakeFlow:
    """Stands in for InstalledAppFlow; records how it was driven."""

    def __init__(self, secret_path: Path, scopes: list[str]) -> None:
        self.secret_path = secret_path
        self.scopes = scopes
        self.kwargs: dict[str, Any] = {}

    def run_local_server(self, **kwargs: Any) -> Credentials:  # noqa: ANN401
        self.kwargs = kwargs
        return make_creds()


def fail_if_called(secret_path: Path, scopes: list[str]) -> Any:  # noqa: ANN401
    raise AssertionError("the OAuth flow must not start")


def write_client_secret(settings: Settings) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.client_secret_path.write_text("{}")


def test_scopes_for_gmail() -> None:
    assert scopes_for({"gmail"}) == ["https://www.googleapis.com/auth/gmail.modify"]


def test_scopes_for_no_modules_is_empty() -> None:
    assert scopes_for(set()) == []


def test_save_token_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "token.json"
    save_token(make_creds(), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert list(path.parent.iterdir()) == [path]


def test_save_token_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    path.write_text("old")
    save_token(make_creds(), path)
    assert "fake-refresh-token" in path.read_text()
    assert list(tmp_path.iterdir()) == [path]


def test_missing_token_asks_for_login(settings: Settings) -> None:
    with pytest.raises(AuthError, match="Not logged in.*google-personal-mcp login"):
        load_credentials(settings)


@pytest.mark.parametrize("content", ["", "{not json", '{"token": "x"}', "null", "[]", '"x"'])
def test_corrupted_token_asks_for_login(settings: Settings, content: str) -> None:
    settings.token_path.parent.mkdir(parents=True)
    settings.token_path.write_text(content)
    with pytest.raises(AuthError, match="google-personal-mcp login"):
        load_credentials(settings)


def test_token_missing_scope_asks_for_login(settings: Settings) -> None:
    save_token(make_creds(scopes=["https://www.googleapis.com/auth/calendar.events"]), settings.token_path)
    with pytest.raises(AuthError, match="scope.*google-personal-mcp login"):
        load_credentials(settings)


def test_valid_token_is_returned_without_refresh(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    save_token(make_creds(), settings.token_path)

    def no_refresh(self: Credentials, request: object) -> None:
        raise AssertionError("a valid token must not be refreshed")

    monkeypatch.setattr(Credentials, "refresh", no_refresh)
    assert load_credentials(settings).valid


def test_expired_token_is_refreshed_and_saved(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    save_token(make_creds(expired=True), settings.token_path)

    def fake_refresh(self: Credentials, request: object) -> None:
        self.token = "refreshed-access-token"  # noqa: S105
        self.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    credentials = load_credentials(settings, request=object())  # type: ignore[arg-type]
    assert credentials.valid
    assert credentials.token == "refreshed-access-token"  # noqa: S105
    assert "refreshed-access-token" in settings.token_path.read_text()
    assert stat.S_IMODE(settings.token_path.stat().st_mode) == 0o600


def test_revoked_token_explains_testing_expiry(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    save_token(make_creds(expired=True), settings.token_path)

    def revoked(self: Credentials, request: object) -> None:
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", revoked)
    with pytest.raises(AuthError, match="expired or was revoked.*7 days.*google-personal-mcp login"):
        load_credentials(settings, request=object())  # type: ignore[arg-type]


def test_network_failure_during_refresh_is_reported(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    save_token(make_creds(expired=True), settings.token_path)

    def offline(self: Credentials, request: object) -> None:
        raise TransportError("no route")

    monkeypatch.setattr(Credentials, "refresh", offline)
    with pytest.raises(AuthError, match="Could not reach Google.*google-personal-mcp login"):
        load_credentials(settings, request=object())  # type: ignore[arg-type]


def test_unsaved_refreshed_token_is_a_readable_error(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    save_token(make_creds(expired=True), settings.token_path)

    def fake_refresh(self: Credentials, request: object) -> None:
        self.token = "refreshed-access-token"  # noqa: S105
        self.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    def read_only_disk(credentials: Credentials, path: Path) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    monkeypatch.setattr(auth, "save_token", read_only_disk)
    with pytest.raises(AuthError, match="could not be saved.*Permission denied.*google-personal-mcp login"):
        load_credentials(settings, request=object())  # type: ignore[arg-type]


def test_login_hint_names_the_command_the_folder_and_the_account_variable() -> None:
    assert LOGIN_HINT == (
        "Run `uv run google-personal-mcp login` from this server's repo folder, with GOOGLE_PERSONAL_MCP_ACCOUNT "
        "set (see the README)."
    )


def test_account_guard_caches_and_rejects_mismatch() -> None:
    calls: list[int] = []
    guard = AccountGuard()
    for _ in range(2):
        with pytest.raises(AuthError, match="other@example.com.*user@example.com"):
            guard.check(make_creds(), expected=ACCOUNT, fetch_email=lambda: calls.append(1) or "other@example.com")
    assert len(calls) == 1


def test_account_guard_accepts_matching_account_case_insensitively() -> None:
    calls: list[int] = []
    guard = AccountGuard()
    for _ in range(2):
        guard.check(make_creds(), expected=ACCOUNT, fetch_email=lambda: calls.append(1) or "User@Example.com")
    assert len(calls) == 1


def test_account_guard_rechecks_when_the_refresh_token_changes() -> None:
    guard = AccountGuard()
    with pytest.raises(AuthError, match="other@example.com"):
        guard.check(make_creds(), expected=ACCOUNT, fetch_email=lambda: "other@example.com")
    relogged = make_creds(refresh_token="fake-refresh-token-2")  # noqa: S106
    guard.check(relogged, expected=ACCOUNT, fetch_email=lambda: ACCOUNT)
    with pytest.raises(AuthError, match="other@example.com"):
        guard.check(make_creds(), expected=ACCOUNT, fetch_email=lambda: "other@example.com")


def test_account_guard_fetches_once_under_concurrent_checks() -> None:
    guard = AccountGuard()
    calls: list[int] = []
    inside, release = threading.Event(), threading.Event()

    def slow_fetch() -> str:
        calls.append(1)
        inside.set()
        release.wait(timeout=5)
        return ACCOUNT

    def check() -> None:
        guard.check(make_creds(), expected=ACCOUNT, fetch_email=slow_fetch)

    first, second = threading.Thread(target=check), threading.Thread(target=check)
    first.start()
    assert inside.wait(timeout=5)
    second.start()
    second.join(timeout=0.2)  # with the lock, the second check waits for the first fetch
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert len(calls) == 1


def test_login_requires_account(settings_without_account: Settings) -> None:
    with pytest.raises(ConfigError, match="GOOGLE_PERSONAL_MCP_ACCOUNT"):
        run_login(settings_without_account, fetch_email=lambda c: "x", flow_factory=fail_if_called)


def test_login_missing_client_secret(settings: Settings) -> None:
    with pytest.raises(AuthError, match=r"client_secret\.json.*google-personal-mcp login"):
        run_login(settings, fetch_email=lambda c: ACCOUNT, flow_factory=fail_if_called)


def test_login_passes_login_hint_and_saves(settings: Settings) -> None:
    write_client_secret(settings)
    flows: list[FakeFlow] = []

    def factory(secret_path: Path, scopes: list[str]) -> FakeFlow:
        flows.append(FakeFlow(secret_path, scopes))
        return flows[0]

    email = run_login(settings, fetch_email=lambda c: ACCOUNT, flow_factory=factory)  # type: ignore[arg-type]
    assert email == ACCOUNT
    assert flows[0].secret_path == settings.client_secret_path
    assert flows[0].scopes == [GMAIL_MODIFY_SCOPE]
    assert flows[0].kwargs["login_hint"] == ACCOUNT
    assert flows[0].kwargs["port"] == 0
    assert flows[0].kwargs["open_browser"] is True
    assert flows[0].kwargs["prompt"] == "consent"
    assert settings.token_path.exists()


class FlowWithoutRefreshToken(FakeFlow):
    def run_local_server(self, **kwargs: Any) -> Credentials:  # noqa: ANN401
        self.kwargs = kwargs
        return make_creds(refresh_token=None)


def test_login_without_refresh_token_does_not_save(settings: Settings) -> None:
    write_client_secret(settings)
    with pytest.raises(AuthError, match="no refresh token.*not saved.*google-personal-mcp login"):
        run_login(settings, fetch_email=lambda c: ACCOUNT, flow_factory=FlowWithoutRefreshToken)  # type: ignore[arg-type]
    assert not settings.token_path.exists()


def test_login_wrong_account_does_not_save(settings: Settings) -> None:
    write_client_secret(settings)
    with pytest.raises(AuthError, match="other@example.com"):
        run_login(settings, fetch_email=lambda c: "other@example.com", flow_factory=FakeFlow)  # type: ignore[arg-type]
    assert not settings.token_path.exists()
