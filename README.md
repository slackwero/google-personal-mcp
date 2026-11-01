# google-personal-mcp

A local MCP server (stdio) that lets Claude Code read and organize the Gmail mailbox and the
Google Calendar of **one personal Google account**, using Google's public APIs and an OAuth
client you create yourself. Main use: cleaning a cluttered mailbox by finding old, unimportant
and heavy emails, labeling them and moving them to the trash; and reading and managing your own
calendar events.

There is no sending, replying or permanent deletion of mail, and no inviting of guests. The
scopes requested are listed under [Scopes](#scopes).

## Tools

### Gmail

| Kind | Tools |
|---|---|
| Read | `gmail_search`, `gmail_get_message`, `gmail_get_thread`, `gmail_list_labels`, `gmail_top_senders` |
| Write | `gmail_create_label`, `gmail_modify_labels`, `gmail_trash`, `gmail_untrash`, `gmail_download_attachment` |

Bulk write tools take either `message_ids` (1 to 1,000) or `query` plus `max_messages`
(1 to 1,000). `gmail_modify_labels` rejects the `TRASH`, `SPAM`, `SENT` and `DRAFT` labels.

In query mode `max_messages` caps the messages the query matches, not only the ones that
change, so a query for `gmail_modify_labels` should exclude mail that is already done (for
example `in:inbox`, `is:unread` or `-label:X`). Archiving or marking as read with nothing else
added, trashing and untrashing exclude it on their own. `more_matching: true` in a result means
more mail matched than `max_messages`: call again to continue.

### Calendar

| Kind | Tools |
|---|---|
| Read | `calendar_list_calendars`, `calendar_list_events`, `calendar_get_event`, `calendar_freebusy` |
| Write | `calendar_create_event`, `calendar_update_event`, `calendar_delete_event` |

- `calendar_list_calendars` shows each calendar's time zone and whether this module can write
  to it. `calendar_list_events` lists one calendar's events in a time window, one page at a
  time (`max_results` 1 to 250, default 50; pass the returned `next_page_token` to continue),
  with recurring events expanded into occurrences. The default window is from now for 7 days.
  `calendar_get_event` returns one event with its description, guests and conference link.
  `calendar_freebusy` returns the busy intervals of 1 to 20 calendars (default: the primary
  one), without event details.
- Writes never send invitations or notifications and never add guests. Only events on calendars
  the account owns can be changed, and only regular events (not birthdays, focus time,
  out-of-office or working-location events) that have no other guests, are organized by the
  account, and are not cancelled or locked. The `editable` flag in read results says whether a
  write would be accepted.
- Limits: `summary` 1 to 1,024 characters, `description` up to 8,192, `location` up to 1,024.
  `recurrence` takes RFC 5545 lines such as `RRULE:FREQ=WEEKLY;COUNT=10`.
- A recurring series is changed or deleted only with `series: true`; without it, use an
  occurrence ID from `calendar_list_events` to change one occurrence alone.

### Time formats

`start`, `end`, `time_min` and `time_max` accept three forms: `2026-11-03` (a date, which makes
an all-day event when creating), `2026-11-03T10:00` (local time) or
`2026-11-03T10:00:00-03:00` (with an offset). Times without an offset are read in `time_zone`
when you pass one, otherwise in the calendar's own time zone (for `calendar_freebusy`, the
primary calendar's). The end of an all-day event is exclusive: a one-day event on 3 November
ends on 4 November, and `end` defaults to the next day. Google rejects an unreasonable
`calendar_freebusy` range with its own error; this server does not cap it.

## Scopes

`login` requests only the scopes of the enabled modules:

| Module | Scope (`https://www.googleapis.com/auth/…`) | Used for |
|---|---|---|
| Gmail | `gmail.modify` | Read mail, labels, trash and restore |
| Calendar | `calendar.calendarlist.readonly` | List calendars and their time zones |
| Calendar | `calendar.events.readonly` | Read events on all calendars |
| Calendar | `calendar.events.owned` | Create, change and delete events on calendars you own |
| Calendar | `calendar.events.freebusy` | Free/busy queries, which the read scope does not cover |

Google's consent screen lets you untick individual scopes. `login` then saves the token and
reports which modules it cannot serve; the other module keeps working.

## Google Cloud setup

Do this once, signed in with the Google account the server will manage.

1. Create a Google Cloud project in the web console (no organization or billing account needed).
2. Enable the **Gmail API** and the **Google Calendar API**.
3. In **Google Auth Platform**, configure the consent screen as **External** and add your
   account as a test user.
4. Create an OAuth client of type **Desktop app** and download its JSON right away: the
   console shows the client secret only once.
5. Save it as `client_secret.json` in the config directory (default `~/.config/google-personal-mcp`,
   directory mode 700, file mode 600).

Gmail API and Google Calendar API use is currently free of charge for personal use (Google has announced possible charges only above very high quotas).

## Install and log in

```bash
cd <path to this repo>
uv sync
GOOGLE_PERSONAL_MCP_ACCOUNT=<your Gmail address> uv run google-personal-mcp login
```

`login` opens the browser, checks that the authorized account matches
`GOOGLE_PERSONAL_MCP_ACCOUNT`, and stores the token in the config directory with mode 600.
Exit codes: `0` ok, `1` authentication error, `2` configuration error.

Both modules are enabled by default (`GOOGLE_PERSONAL_MCP_MODULES=gmail,calendar`). To keep
Gmail only, set `GOOGLE_PERSONAL_MCP_MODULES=gmail` for `login` and in the `claude mcp add`
command below; only the Gmail scope is then requested. If you logged in before Calendar
existed, run `login` once more to grant the Calendar scopes: Gmail keeps working with the old
token, and Calendar tools ask for the new login.

While the OAuth app is in **Testing**, the refresh token expires after **7 days**: run
`login` again when the server reports an expired or revoked token.

## Register with Claude Code

Run this from the folder where you will use the MCP:

```bash
claude mcp add --scope local google-personal \
  -e GOOGLE_PERSONAL_MCP_ACCOUNT=<your Gmail address> \
  -- uv run --directory <path to this repo> google-personal-mcp serve
```

Always use `--scope local`. Never use `--scope user`: a user-scoped server would expose your
mailbox in every other project's sessions. Do not use `--scope project` either: it writes a
`.mcp.json` meant to be committed, which would carry your Gmail address and the local path to
this repo.

## Permissions

Reads can be auto-approved; writes must ask every time. Put this in `.claude/settings.local.json`
in the folder where the MCP is used:

```json
{
  "permissions": {
    "allow": [
      "mcp__google-personal__gmail_search",
      "mcp__google-personal__gmail_get_message",
      "mcp__google-personal__gmail_get_thread",
      "mcp__google-personal__gmail_list_labels",
      "mcp__google-personal__gmail_top_senders",
      "mcp__google-personal__calendar_list_calendars",
      "mcp__google-personal__calendar_list_events",
      "mcp__google-personal__calendar_get_event",
      "mcp__google-personal__calendar_freebusy"
    ],
    "ask": [
      "mcp__google-personal__gmail_create_label",
      "mcp__google-personal__gmail_modify_labels",
      "mcp__google-personal__gmail_trash",
      "mcp__google-personal__gmail_untrash",
      "mcp__google-personal__gmail_download_attachment",
      "mcp__google-personal__calendar_create_event",
      "mcp__google-personal__calendar_update_event",
      "mcp__google-personal__calendar_delete_event"
    ]
  }
}
```

The `ask` rules are needed even though the write tools are absent from `allow`: an `ask` rule
still prompts in auto mode and after a mistaken "don't ask again".

**Warning:** `bypassPermissions` mode skips every prompt. Do not use this MCP in that mode.

Email and event content can carry prompt injection, so in the same folder also deny every other MCP
server you do not need there, above all those that can write or reach the network (work
tools, file sharing, browsers). A deny rule wins over any allow rule and removes the tools
from the session, for example:

```json
"deny": ["mcp__gitlab__*", "mcp__claude_ai_Google_Drive__*", "mcp__plugin_playwright_playwright__*"]
```

Characters such as `.` or spaces in a server name become `_` (`claude.ai Gmail` is
`mcp__claude_ai_Gmail__*`).

## Undo

Every write result returns the IDs it changed. Trashed mail stays in Gmail's trash for 30 days.
To undo a trash exactly, pass the `inbox_ids` returned by `gmail_trash` to `gmail_untrash` as
`restore_inbox`: Gmail does not put restored mail back in the inbox on its own.

Calendar writes can be undone from their results:

- `calendar_create_event` returns the created event: delete it with `calendar_delete_event`.
- `calendar_update_event` returns `before` (the previous values, named like the tool's
  parameters) and the event after the change: pass `before` back to the same tool to undo it.
- `calendar_delete_event` returns the deleted event, complete enough to re-create it with
  `calendar_create_event`; the new event gets a new ID. Google Calendar also keeps the deleted
  event in that calendar's trash for 30 days.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_PERSONAL_MCP_ACCOUNT` | none (required) | The Gmail address this server manages. Required by both `login` and `serve`: `serve` starts without it, but every tool call then returns a config error. |
| `GOOGLE_PERSONAL_MCP_MODULES` | `gmail,calendar` | Comma-separated modules to enable (`gmail`, `calendar`). |
| `GOOGLE_PERSONAL_MCP_CONFIG_DIR` | `~/.config/google-personal-mcp` | Holds `client_secret.json` and `token.json`. Must be absolute or start with `~/`. |
| `GOOGLE_PERSONAL_MCP_DOWNLOAD_DIR` | `~/Downloads/google-personal-mcp` | Where attachments are saved. Must be absolute or start with `~/`, so files never land in this repo. |

## Security

- Email content (subjects, senders, bodies, attachment names) and event content (titles,
  descriptions, locations, guest names and addresses, conference links) is untrusted input and
  may contain prompt injection: anyone can send you an invitation. Tools return it as data.
- No secrets live in this repo. The client secret and the token stay in the config directory;
  the token file has mode 600.
- Attachments are downloaded only on explicit request, only into the download directory
  (created with mode 0700), with sanitized names, and an existing file is never overwritten.
- Downloaded files are not quarantined by macOS Gatekeeper, so treat them as untrusted.

## Privacy

The server runs locally and sends data only between your computer and Google's APIs. See
[PRIVACY.md](PRIVACY.md).
