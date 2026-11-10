# google-personal-mcp

[![platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-blue)](#requirements)
[![python](https://img.shields.io/badge/python-%E2%89%A53.15-3776AB)](pyproject.toml)
[![MCP tools](https://img.shields.io/badge/MCP%20tools-17-blue)](#tools)
[![Google](https://img.shields.io/badge/Google-Gmail%20%7C%20Calendar-4285F4)](#scopes)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![Built with Claude Code](https://img.shields.io/badge/Built_with-Claude_Code-D97757?logo=claude&logoColor=white)](https://claude.com/product/claude-code)

[![CI](https://github.com/slackwero/google-personal-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/slackwero/google-personal-mcp/actions/workflows/ci.yml)
[![Security](https://github.com/slackwero/google-personal-mcp/actions/workflows/security.yml/badge.svg)](https://github.com/slackwero/google-personal-mcp/actions/workflows/security.yml)
[![CodeQL](https://github.com/slackwero/google-personal-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/slackwero/google-personal-mcp/actions/workflows/codeql.yml)

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
- Writes never send invitations or notifications and never add guests. Only calendars the account
  owns can be written to: its primary calendar and the secondary calendars it owns. A calendar
  someone else shares with you stays read-only, even with permission to make changes and manage
  sharing. On those calendars, only regular events (not birthdays, focus time,
  out-of-office or working-location events) that have no other guests, are organized by the
  account, and are not cancelled or locked. An event whose guest list Google returns incomplete
  counts as having other guests. The `editable` flag in read results says whether a write would
  be accepted.
- Limits: `summary` 1 to 1,024 characters, `description` up to 8,192, `location` up to 1,024.
  `recurrence` takes RFC 5545 lines such as `RRULE:FREQ=WEEKLY;COUNT=10`.
- A recurring series is changed or deleted only with `series: true`; without it, use an
  occurrence ID from `calendar_list_events` to change one occurrence alone. A series write also
  checks every occurrence that was edited on its own (Google keeps those as separate events)
  and is refused if any of them could not be changed, or if they cannot be listed. On a series,
  `editable` reflects only the main event, not those occurrences.

### Time formats

`start`, `end`, `time_min` and `time_max` accept three forms: `2026-11-03` (a date, which makes
an all-day event when creating), `2026-11-03T10:00` (local time) or
`2026-11-03T10:00:00-03:00` (with an offset). Times without an offset are read in `time_zone`
when you pass one, otherwise in the calendar's own time zone (for `calendar_freebusy`, the
primary calendar's). `calendar_update_event` is the exception: it reads them in `time_zone`
when you pass one, otherwise in the event's own time zone, otherwise in the calendar's, so an
event moved in its own zone keeps its wall-clock meaning; read results show each event's
`time_zone`. The end of an all-day event is exclusive: a one-day event on 3 November
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

## Requirements

- Linux or macOS.
- [uv](https://docs.astral.sh/uv/), which installs Python 3.15 and the dependencies for you.
- [Claude Code](https://claude.com/product/claude-code), or another MCP client.
- A Google account (a personal `@gmail.com` account works) and a web browser, for the one-time
  setup and the login.

## Google Cloud setup

The server talks to Google through **your own** Google Cloud project and OAuth client, so your
mail and calendar never go through anyone else's app. The setup takes about ten minutes and
costs nothing.

Do every step signed in with the Google account the server will manage. If your browser is
signed in to several Google accounts, check the account in the top-right corner of the console
before each step.

### 1. Create a project

1. Open [Create a project](https://console.cloud.google.com/projectcreate).
2. Give it a name, for example `google-personal-mcp`. A personal account has no organization:
   leave **Location** as **No organization**. No billing account is needed.
3. Click **Create**, then make sure the new project is selected in the project picker at the
   top of the console. Every following step applies to the selected project.

### 2. Enable the APIs

Enable the API of each module you will use, and click **Enable** on each page:

- [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com) for the Gmail
  module.
- [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
  for the Calendar module.

### 3. Configure the consent screen

1. Open **Menu → Google Auth Platform → [Branding](https://console.developers.google.com/auth/branding)**.
   If the page says *Google Auth platform not configured yet*, click **Get started**.
2. **App information**: an **App name** (you will see it on Google's consent screen, for
   example `google-personal-mcp`) and your address as **User support email**. Click **Next**.
3. **Audience**: choose **External**. *Internal* is only available to Google Workspace
   organizations. Click **Next**.
4. **Contact information**: your email address. Click **Next**.
5. **Finish**: tick *I agree to the Google API Services: User Data Policy*, click **Continue**,
   then **Create**.
6. Open **[Audience](https://console.developers.google.com/auth/audience)**. Under
   **Test users**, click **Add users**, enter the Google account the server will manage, and
   click **Save**. A new app starts in **Testing**, and only its test users can log in.

You do not need to add scopes under **Data Access**: `login` requests them at run time (see
[Scopes](#scopes)). Declaring them only matters if you submit the app for verification, which
this setup does not need.

### 4. Create the OAuth client

1. Open **[Clients](https://console.developers.google.com/auth/clients)** and click
   **Create client**.
2. **Application type**: **Desktop app**. **Name**: anything; it is only shown in the console.
   The optional *Use this client for an AI-powered agent* setting is not needed.
3. Click **Create**. In the dialog that follows, click **Download JSON** right away: the
   console shows the client secret only when the client is created. If you miss it, create
   another client.

A *Desktop app* client uses a loopback redirect (`http://localhost` on a free port), which is
what `login` uses. A *Web application* client does not work with this server.

### 5. Save the client file

Move the downloaded file into the config directory, readable only by you:

```bash
mkdir -p ~/.config/google-personal-mcp
chmod 700 ~/.config/google-personal-mcp
mv ~/Downloads/client_secret_*.json ~/.config/google-personal-mcp/client_secret.json
chmod 600 ~/.config/google-personal-mcp/client_secret.json
```

To use another directory, set `GOOGLE_PERSONAL_MCP_CONFIG_DIR` (see
[Configuration](#configuration)). Never put this file in a repository: it identifies your OAuth
client. This repo's `.gitignore` blocks `client_secret*.json` and `token*.json` as a safety net.

### 6. Choose the publishing status

The app starts in **Testing**. You can leave it there or publish it without verification:

| | Testing | In production, unverified |
|---|---|---|
| Who can log in | Only the test users | Any Google account, up to 100 users |
| How long a login lasts | 7 days, then run `login` again | No fixed expiry (see [Keeping access working](#keeping-access-working)) |
| Extra setup | None | Branding: home page, privacy policy and authorized domain |

To publish without verification:

1. In **Branding**, fill in the **Application home page** and the **Application privacy policy
   link**, add the domain of those two links under **Authorized domains**, and fill in the
   **Developer contact information**. Do not upload a logo: an app with a logo needs Google's
   brand verification.
2. In **Audience**, click **Publish app** and confirm. Do not submit the app for verification:
   this server works as an unverified app.
3. Run `login` again (next section), so the new token is issued for the published app.

Gmail API and Google Calendar API use is free of charge for normal use. Google has announced
charges only above very high daily quotas.

## Install and log in

```bash
git clone https://github.com/slackwero/google-personal-mcp.git
cd google-personal-mcp
uv sync --locked
GOOGLE_PERSONAL_MCP_ACCOUNT=<your Gmail address> uv run --locked google-personal-mcp login
```

This installs the latest code on `main`. To install a release instead, clone its tag with
`git clone --branch <tag>` (for example `--branch v1.0.0`). Each
[release](https://github.com/slackwero/google-personal-mcp/releases) also carries the built
package (wheel and sdist), with SHA-256 checksums and signed build provenance that you can check
with `gh attestation verify <file> --repo slackwero/google-personal-mcp`.

What `login` does:

1. It opens your browser on Google's sign-in page for that account.
2. Google may warn that it *hasn't verified this app*. It is your own OAuth client from the
   steps above: click **Continue** (or **Advanced**, then **Go to** your app's name).
3. Google lists the access requested. You can untick the scopes of a module you do not want
   (see [Scopes](#scopes)).
4. The browser says the authentication flow has completed, and you can close it.
5. The terminal prints `Logged in as <address>`, plus any module that got no access.

`login` checks that the account you authorized is `GOOGLE_PERSONAL_MCP_ACCOUNT` and stores the
token as `token.json` in the config directory, with mode 600. Exit codes: `0` ok, `1`
authentication error, `2` configuration error. The server itself never opens a browser: when the
token is missing, expired or revoked, its tools return an error that asks you to run `login`
again.

Both modules are enabled by default (`GOOGLE_PERSONAL_MCP_MODULES=gmail,calendar`). To keep
Gmail only, set `GOOGLE_PERSONAL_MCP_MODULES=gmail` for `login` and in the `claude mcp add`
command below; only the Gmail scope is then requested. If you logged in before Calendar
existed, run `login` once more to grant the Calendar scopes: Gmail keeps working with the old
token, and Calendar tools ask for the new login.

### Keeping access working

Google stops accepting the token, and `login` must be run again, when:

- the app is in **Testing** and 7 days have passed since the login;
- the token goes unused for 6 months;
- you change the account's password, if the token includes the Gmail scope;
- you revoke the access, or the same OAuth client gets more than 100 tokens for your account
  (the oldest one stops working).

Google also deletes an OAuth client that is unused for 6 months. You can restore it from
**Clients** within 30 days, or create a new one and repeat steps 4 and 5.

### Revoking access

Remove the app at [Third-party connections](https://myaccount.google.com/connections), then
delete `token.json` from the config directory.

## Register with Claude Code

Run this from the folder where you will use the MCP:

```bash
claude mcp add --scope local google-personal \
  -e GOOGLE_PERSONAL_MCP_ACCOUNT=<your Gmail address> \
  -- uv run --locked --directory <path to this repo> google-personal-mcp serve
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

### Keep your mail from leaving the session

The read tools run without a prompt, and what they return can carry hidden instructions. The
risk is the combination: private mail, untrusted content, and a way to send data out. Other
MCP servers are not the only way out: Claude Code's own `WebFetch`, `WebSearch` and `Bash`
(`curl`, `wget`, `nc`, `open <url>`, `git push`, …) can reach the network too, and none of them
goes through this server's `ask` rules. In the folder where you use this MCP, deny them as well:

```json
"deny": ["WebFetch", "WebSearch", "Bash(curl:*)", "Bash(wget:*)", "Bash(nc:*)", "Bash(open:*)"]
```

The `Bash` patterns are only a partial guard, because a shell has many other ways to reach the
network. The real protection is that `Bash` always asks in that folder: keep it out of
`allow`, and read every command before you approve it.

## Undo

Every write result returns the IDs it changed. Trashed mail stays in Gmail's trash for 30 days.
To undo a trash exactly, pass the `inbox_ids` returned by `gmail_trash` to `gmail_untrash` as
`restore_inbox`: Gmail does not put restored mail back in the inbox on its own.

Calendar writes can be undone from their results:

- `calendar_create_event` returns the created event: delete it with `calendar_delete_event`.
- `calendar_update_event` returns `before` (the previous values, named like the tool's
  parameters) and the event after the change: pass `before` back to the same tool to undo it.
- `calendar_delete_event` returns the deleted event, so it can be re-created with
  `calendar_create_event`; the new event gets a new ID. Google Calendar also keeps the deleted
  event in that calendar's trash for 30 days. A re-create is not an exact copy:
  - a re-created series loses the occurrences that had been edited one by one;
  - it does not restore reminders, color, visibility, free/busy transparency or conference
    data;
  - a snapshot description longer than 8,192 characters cannot be passed to
    `calendar_create_event` as is.

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
  (created with mode 0700), and an existing file is never overwritten. The sender chooses the
  name, so it is sanitized: path parts and control characters are dropped, shell characters
  such as `$`, backticks, quotes, `;`, `|` or `*` become `_`, and a leading `-` gets a `_`
  in front.
- Downloaded files are not quarantined by macOS Gatekeeper, so treat them as untrusted.

## Privacy

The server runs locally and sends data only between your computer and Google's APIs. See
[PRIVACY.md](PRIVACY.md).

## License

[MIT](LICENSE).
