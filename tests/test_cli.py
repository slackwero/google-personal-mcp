import pytest
from mcp.server import MCPServer

from google_personal_mcp import cli
from google_personal_mcp.auth import AuthError
from google_personal_mcp.cli import main
from google_personal_mcp.config import load_settings
from google_personal_mcp.server import build_server


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
