import pytest
from mcp.server import MCPServer

from google_personal_mcp import cli, server
from google_personal_mcp.auth import AuthError
from google_personal_mcp.cli import main
from google_personal_mcp.config import ConfigError, load_settings
from google_personal_mcp.server import build_server, make_client_provider


@pytest.fixture(autouse=True)
def _account(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_PERSONAL_MCP_ACCOUNT", "user@example.com")


def test_login_without_account_fails_with_message(capsys, monkeypatch):
    monkeypatch.delenv("GOOGLE_PERSONAL_MCP_ACCOUNT", raising=False)
    assert main(["login"]) == 2
    assert "GOOGLE_PERSONAL_MCP_ACCOUNT" in capsys.readouterr().err


def test_login_success_prints_account(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_login", lambda settings, *, fetch_email: "user@example.com")
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

    def get_profile_email(self) -> str:
        self.profile_calls += 1
        return self.email


@pytest.fixture
def provider_env(monkeypatch):
    state = {"loads": 0, "built": [], "email": "user@example.com"}

    def fake_load(settings):
        state["loads"] += 1
        return object()

    def fake_from_credentials(credentials, limiter):
        client = _FakeClient(state["email"])
        state["built"].append((client, limiter))
        return client

    monkeypatch.setattr(server, "load_credentials", fake_load)
    monkeypatch.setattr(server.GmailClient, "from_credentials", staticmethod(fake_from_credentials))
    return state


def test_provider_requires_account(provider_env):
    provider = make_client_provider(load_settings({}))
    with pytest.raises(ConfigError, match="GOOGLE_PERSONAL_MCP_ACCOUNT"):
        provider()
    assert provider_env["built"] == []


def test_provider_builds_nothing_until_first_call(provider_env):
    provider = make_client_provider(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"}))
    assert provider_env["loads"] == 0
    assert provider_env["built"] == []
    provider()
    assert provider_env["loads"] == 1
    assert len(provider_env["built"]) == 1


def test_provider_reuses_client_and_checks_account_once(provider_env):
    provider = make_client_provider(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"}))
    first = provider()
    assert provider() is first
    assert provider() is first
    assert len(provider_env["built"]) == 1
    assert first.profile_calls == 1
    assert provider_env["loads"] == 3  # credentials are reloaded on every call


def test_provider_rejects_mismatched_account_on_every_call(provider_env):
    provider_env["email"] = "other@example.com"
    provider = make_client_provider(load_settings({"GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"}))
    for _ in range(3):
        with pytest.raises(AuthError, match="other@example.com"):
            provider()
    assert provider_env["built"][0][0].profile_calls == 1
