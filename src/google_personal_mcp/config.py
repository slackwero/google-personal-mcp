"""Runtime settings, read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

KNOWN_MODULES: frozenset[str] = frozenset({"gmail"})

_ENV_MODULES = "GOOGLE_PERSONAL_MCP_MODULES"
_ENV_CONFIG_DIR = "GOOGLE_PERSONAL_MCP_CONFIG_DIR"
_ENV_DOWNLOAD_DIR = "GOOGLE_PERSONAL_MCP_DOWNLOAD_DIR"
_ENV_ACCOUNT = "GOOGLE_PERSONAL_MCP_ACCOUNT"


class ConfigError(Exception):
    """The configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    modules: frozenset[str]
    config_dir: Path
    download_dir: Path
    account: str | None

    @property
    def client_secret_path(self) -> Path:
        return self.config_dir / "client_secret.json"

    @property
    def token_path(self) -> Path:
        return self.config_dir / "token.json"


def _parse_modules(raw: str | None) -> frozenset[str]:
    if raw is None:
        return KNOWN_MODULES
    modules = frozenset(name.strip() for name in raw.split(",") if name.strip())
    unknown = modules - KNOWN_MODULES
    if unknown:
        raise ConfigError(f"Unknown module(s) in {_ENV_MODULES}: {', '.join(sorted(unknown))}")
    return modules


def _resolve_path(raw: str | None, default: Path, home: Path) -> Path:
    if not raw:
        return default
    if raw == "~" or raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from `env` (defaults to os.environ).

    `~` expands against the mapping's HOME, falling back to the real home directory.
    """
    values = os.environ if env is None else env
    home = Path(values["HOME"]) if "HOME" in values else Path.home()
    account = values.get(_ENV_ACCOUNT, "").strip().lower() or None
    return Settings(
        modules=_parse_modules(values.get(_ENV_MODULES)),
        config_dir=_resolve_path(values.get(_ENV_CONFIG_DIR), home / ".config" / "google-personal-mcp", home),
        download_dir=_resolve_path(values.get(_ENV_DOWNLOAD_DIR), home / "Downloads" / "google-personal-mcp", home),
        account=account,
    )


def require_account(settings: Settings) -> str:
    """Return the configured account, or fail with an actionable error."""
    if settings.account is None:
        raise ConfigError(f"{_ENV_ACCOUNT} is not set; set it to the Gmail address this server manages")
    return settings.account
