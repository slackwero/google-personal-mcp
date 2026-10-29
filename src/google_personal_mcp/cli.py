"""Command line entry point: `login` runs the OAuth flow, `serve` runs the MCP server on stdio."""

import argparse
import logging
import sys
from collections.abc import Sequence

from google.oauth2.credentials import Credentials

from google_personal_mcp.auth import AuthError, run_login
from google_personal_mcp.config import ConfigError, load_settings
from google_personal_mcp.gmail.client import GmailClient
from google_personal_mcp.quota import QuotaLimiter
from google_personal_mcp.server import build_server

EXIT_OK = 0
EXIT_AUTH_ERROR = 1
EXIT_CONFIG_ERROR = 2


def _fetch_email(credentials: Credentials) -> str:
    return GmailClient.from_credentials(credentials, QuotaLimiter()).get_profile_email()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="google-personal-mcp", description="Personal Gmail MCP server.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="authorize the configured Google account in the browser")
    sub.add_parser("serve", help="run the MCP server on stdio")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    args = _parser().parse_args(argv)  # exits with code 2 on a usage error
    try:
        settings = load_settings()
        if args.command == "login":
            email = run_login(settings, fetch_email=_fetch_email)
            print(f"Logged in as {email}")
        else:
            build_server(settings).run()
    except AuthError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return EXIT_AUTH_ERROR
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return EXIT_OK
