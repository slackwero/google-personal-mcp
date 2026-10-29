import pytest

from google_personal_mcp.config import ConfigError, load_settings, require_account


def test_defaults(tmp_path):
    s = load_settings({"HOME": str(tmp_path)})
    assert s.modules == frozenset({"gmail"})
    assert s.config_dir == tmp_path / ".config" / "google-personal-mcp"
    assert s.download_dir == tmp_path / "Downloads" / "google-personal-mcp"
    assert s.account is None
    assert s.token_path.name == "token.json"
    assert s.client_secret_path.name == "client_secret.json"


def test_overrides_and_tilde(tmp_path):
    env = {
        "HOME": str(tmp_path),
        "GOOGLE_PERSONAL_MCP_CONFIG_DIR": "~/cfg",
        "GOOGLE_PERSONAL_MCP_DOWNLOAD_DIR": "~/dl",
        "GOOGLE_PERSONAL_MCP_ACCOUNT": " User@Example.com ",
    }
    s = load_settings(env)
    assert s.config_dir == tmp_path / "cfg"
    assert s.download_dir == tmp_path / "dl"
    assert s.account == "user@example.com"


def test_empty_account_is_none(tmp_path):
    s = load_settings({"HOME": str(tmp_path), "GOOGLE_PERSONAL_MCP_ACCOUNT": "   "})
    assert s.account is None


def test_modules_parsing():
    assert load_settings({"GOOGLE_PERSONAL_MCP_MODULES": " gmail , "}).modules == frozenset({"gmail"})


def test_unknown_module_is_error():
    with pytest.raises(ConfigError, match="calendar"):
        load_settings({"GOOGLE_PERSONAL_MCP_MODULES": "gmail,calendar"})


def test_require_account_missing():
    with pytest.raises(ConfigError, match="GOOGLE_PERSONAL_MCP_ACCOUNT"):
        require_account(load_settings({}))


def test_require_account_returns_value(tmp_path):
    s = load_settings({"HOME": str(tmp_path), "GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})
    assert require_account(s) == "user@example.com"
