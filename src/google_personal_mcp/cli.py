"""Command line entry point: `login` runs the OAuth flow, `serve` runs the MCP server on stdio."""

import argparse
import logging
import sys
from collections.abc import Sequence

from google.oauth2.credentials import Credentials

from google_personal_mcp.auth import LOGIN_HINT, MODULE_SCOPES, AuthError, run_login
from google_personal_mcp.config import ConfigError, load_settings
from google_personal_mcp.gcalendar.client import CALENDAR_BURST, CALENDAR_REQUESTS_PER_MINUTE, CalendarClient
from google_personal_mcp.gmail.client import GmailClient
from google_personal_mcp.quota import QuotaLimiter
from google_personal_mcp.server import build_server

EXIT_OK = 0
EXIT_AUTH_ERROR = 1
EXIT_CONFIG_ERROR = 2
OAUTH_FLOW_LOGGER = "google_auth_oauthlib.flow"


def _fetch_email(credentials: Credentials) -> str:
    """Return the address of the signed-in account through whichever module the token can use."""
    if credentials.has_scopes(list(MODULE_SCOPES["gmail"])):
        return GmailClient.from_credentials(credentials, QuotaLimiter()).get_profile_email()
    if credentials.has_scopes(["https://www.googleapis.com/auth/calendar.calendarlist.readonly"]):
        limiter = QuotaLimiter(CALENDAR_REQUESTS_PER_MINUTE, capacity=CALENDAR_BURST)
        return CalendarClient.from_credentials(credentials, limiter).get_primary_calendar_id()
    raise AuthError(f"The token has no scope that can identify the account. {LOGIN_HINT}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-personal-mcp", description="Personal Gmail and Google Calendar MCP server."
    )
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
            # The flow logs the redirect request at INFO, and its URL carries the one-time authorization code.
            logging.getLogger(OAUTH_FLOW_LOGGER).setLevel(logging.WARNING)
            result = run_login(settings, fetch_email=_fetch_email)
            print(f"Logged in as {result.email}")
            if result.modules_without_access:
                names = ", ".join(result.modules_without_access)
                print(f"No access was granted for: {names}. Their tools will ask for a new login.")
        else:
            build_server(settings).run()
    except AuthError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return EXIT_AUTH_ERROR
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return EXIT_OK
