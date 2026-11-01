from datetime import UTC, datetime, timedelta

import pytest
from google.oauth2.credentials import Credentials
from mcp.server import MCPServer

from google_personal_mcp import cli, server
from google_personal_mcp.auth import CALENDAR_SCOPES, GMAIL_MODIFY_SCOPE, AuthError, LoginResult, save_token
from google_personal_mcp.cli import main
from google_personal_mcp.config import ConfigError, load_settings
from google_personal_mcp.server import build_server, make_client_providers


@pytest.fixture(autouse=True)
def _account(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_PERSONAL_MCP_ACCOUNT", "user@example.com")


def test_login_without_account_fails_with_message(capsys, monkeypatch):
    monkeypatch.delenv("GOOGLE_PERSONAL_MCP_ACCOUNT", raising=False)
    assert main(["login"]) == 2
    assert "GOOGLE_PERSONAL_MCP_ACCOUNT" in capsys.readouterr().err


def test_login_success_prints_account(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_login", lambda settings, *, fetch_email: LoginResult("user@example.com", []))
    assert main(["login"]) == 0
    assert "user@example.com" in capsys.readouterr().out


def test_login_auth_error_exit_code(capsys, monkeypatch):
    def fail(settings, *, fetch_email):
        raise AuthError("boom")

    monkeypatch.setattr(cli, "run_login", fail)
    assert main(["login"]) == 1
    assert "boom" in capsys.readouterr().err


def test_serve_runs_stdio(monkeypatch):
    calls = []
    monkeypatch.setattr(MCPServer, "run", lambda self, *args, **kwargs: calls.append((args, kwargs)))
    assert main(["serve"]) == 0
    assert calls == [((), {})]


def test_no_subcommand_shows_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_build_server_name():
    assert build_server(load_settings({}), client_provider=lambda: None).name == "google-personal"


def test_serve_starts_without_account(monkeypatch):
    monkeypatch.delenv("GOOGLE_PERSONAL_MCP_ACCOUNT", raising=False)
    calls = []
    monkeypatch.setattr(MCPServer, "run", lambda self, *args, **kwargs: calls.append(args))
    assert main(["serve"]) == 0
    assert calls == [()]


class _FakeClient:
    def __init__(self, email: str) -> None:
        self.email = email
        self.profile_calls = 0
        self.calendar_calls = 0

    def get_profile_email(self) -> str:
        self.profile_calls += 1
        return self.email

    def get_primary_calendar_id(self) -> str:
        self.calendar_calls += 1
        return self.email


class _FakeCredentials:
    def __init__(self, refresh_token: str) -> None:
        self.refresh_token = refresh_token


@pytest.fixture
def provider_env(monkeypatch):
    state = {"loads": 0, "built": [], "email": "user@example.com", "refresh_token": "fake-refresh-1"}

    def fake_load(settings, *, modules=None):
        state["loads"] += 1
        state.setdefault("modules", []).append(list(modules) if modules is not None else None)
        return _FakeCredentials(state["refresh_token"])

    def fake_from_credentials(credentials, limiter):
        client = _FakeClient(state["email"])
        state["built"].append((client, limiter))
        return client

    monkeypatch.setattr(server, "load_credentials", fake_load)
    monkeypatch.setattr(server.GmailClient, "from_credentials", staticmethod(fake_from_credentials))
    monkeypatch.setattr(server.CalendarClient, "from_credentials", staticmethod(fake_from_credentials))
    return state


def _profile_calls(state) -> int:
    return sum(client.profile_calls for client, _ in state["built"])


def _calendar_calls(state) -> int:
    return sum(client.calendar_calls for client, _ in state["built"])


def test_provider_requires_account(provider_env):
    provider = make_client_providers(load_settings({})).gmail
    with pytest.raises(ConfigError, match="GOOGLE_PERSONAL_MCP_ACCOUNT"):
        provider()
    assert provider_env["built"] == []


def test_provider_builds_nothing_until_first_call(provider_env):
    provider = make_client_providers(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})).gmail
    assert provider_env["loads"] == 0
    assert provider_env["built"] == []
    provider()
    assert provider_env["loads"] == 1
    assert len(provider_env["built"]) == 1


def test_provider_builds_a_new_client_per_call_and_shares_the_limiter(provider_env):
    provider = make_client_providers(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})).gmail
    clients = [provider() for _ in range(3)]
    assert [client for client, _ in provider_env["built"]] == clients
    assert len({id(client) for client in clients}) == 3
    assert len({id(limiter) for _, limiter in provider_env["built"]}) == 1
    assert provider_env["loads"] == 3  # credentials are reloaded on every call


def test_provider_checks_the_account_once_per_refresh_token(provider_env):
    provider = make_client_providers(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})).gmail
    for _ in range(3):
        provider()
    assert _profile_calls(provider_env) == 1
    provider_env["refresh_token"] = "fake-refresh-2"  # noqa: S105 - a re-login issues a new refresh token
    for _ in range(3):
        provider()
    assert _profile_calls(provider_env) == 2


def test_provider_rejects_mismatched_account_on_every_call(provider_env):
    provider_env["email"] = "other@example.com"
    provider = make_client_providers(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})).gmail
    for _ in range(3):
        with pytest.raises(AuthError, match="other@example.com"):
            provider()
    assert _profile_calls(provider_env) == 1


def test_relogin_clears_an_account_mismatch_without_a_restart(provider_env):
    provider_env["email"] = "other@example.com"
    provider = make_client_providers(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})).gmail
    with pytest.raises(AuthError, match="other@example.com"):
        provider()
    provider_env["email"], provider_env["refresh_token"] = "user@example.com", "fake-refresh-2"
    provider()
    assert _profile_calls(provider_env) == 2


ENV = {"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"}


def test_calendar_provider_checks_the_account_through_the_primary_calendar(provider_env):
    provider = make_client_providers(load_settings(ENV)).calendar
    provider()
    provider()
    assert _calendar_calls(provider_env) == 1
    assert _profile_calls(provider_env) == 0


def test_calendar_provider_rejects_mismatched_account(provider_env):
    provider_env["email"] = "other@example.com"
    provider = make_client_providers(load_settings(ENV)).calendar
    with pytest.raises(AuthError, match="other@example.com"):
        provider()


def test_one_account_check_serves_both_modules(provider_env):
    providers = make_client_providers(load_settings(ENV))
    providers.gmail()
    providers.calendar()
    assert _profile_calls(provider_env) + _calendar_calls(provider_env) == 1


def test_calendar_provider_loads_credentials_for_calendar_only(provider_env):
    providers = make_client_providers(load_settings(ENV))
    providers.calendar()
    providers.gmail()
    assert provider_env["modules"] == [["calendar"], ["gmail"]]


def test_calendar_provider_has_its_own_limiter(provider_env):
    providers = make_client_providers(load_settings(ENV))
    providers.gmail()
    providers.calendar()
    (_, gmail_limiter), (_, calendar_limiter) = provider_env["built"]
    assert gmail_limiter is not calendar_limiter


def test_gmail_only_token_keeps_gmail_working_and_calendar_asks_for_login(monkeypatch, tmp_path):
    # The default modules are now gmail and calendar, but a token saved before Calendar existed holds Gmail only.
    settings = load_settings({"HOME": str(tmp_path), **ENV})
    save_token(_gmail_only_credentials(), settings.token_path)
    monkeypatch.setattr(server.GmailClient, "from_credentials", staticmethod(lambda c, limiter: _FakeClient(ENV_EMAIL)))
    providers = make_client_providers(settings)
    assert isinstance(providers.gmail(), _FakeClient)
    with pytest.raises(AuthError, match=r"calendar\.events\.owned.*google-personal-mcp login"):
        providers.calendar()


ENV_EMAIL = "user@example.com"


def _gmail_only_credentials() -> Credentials:
    return Credentials(
        token="fake-access-token",  # noqa: S106
        refresh_token="fake-refresh-token",  # noqa: S106
        token_uri="https://oauth2.googleapis.com/token",  # noqa: S106
        client_id="fake-client-id",
        client_secret="fake-client-secret",  # noqa: S106
        scopes=[GMAIL_MODIFY_SCOPE],
        expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
    )


def test_login_warns_about_modules_without_access(capsys, monkeypatch):
    monkeypatch.setattr(
        cli, "run_login", lambda settings, *, fetch_email: LoginResult("user@example.com", ["calendar"])
    )
    assert main(["login"]) == 0
    out = capsys.readouterr().out
    assert "Logged in as user@example.com" in out
    assert "calendar" in out


def _creds_with(scopes: list[str]) -> Credentials:
    credentials = _gmail_only_credentials()
    return Credentials(
        token=credentials.token,
        refresh_token=credentials.refresh_token,
        token_uri=credentials.token_uri,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        scopes=scopes,
    )


def test_fetch_email_uses_gmail_when_granted(monkeypatch):
    monkeypatch.setattr(
        cli.GmailClient, "from_credentials", staticmethod(lambda c, limiter: _FakeClient("g@example.com"))
    )
    assert cli._fetch_email(_creds_with([GMAIL_MODIFY_SCOPE, *CALENDAR_SCOPES])) == "g@example.com"


def test_fetch_email_uses_calendar_when_gmail_was_not_granted(monkeypatch):
    def gmail_must_not_be_built(credentials, limiter):
        raise AssertionError("the Gmail client must not be built")

    monkeypatch.setattr(cli.GmailClient, "from_credentials", staticmethod(gmail_must_not_be_built))
    monkeypatch.setattr(
        cli.CalendarClient, "from_credentials", staticmethod(lambda c, limiter: _FakeClient("c@example.com"))
    )
    assert cli._fetch_email(_creds_with(list(CALENDAR_SCOPES))) == "c@example.com"


def test_fetch_email_without_any_module_scope_fails():
    with pytest.raises(AuthError, match="google-personal-mcp login"):
        cli._fetch_email(_creds_with(["openid"]))
