# Privacy policy

Effective date: 2026-10-31

google-personal-mcp is a local MCP server. It runs on your own computer, started by your MCP
client (for example Claude Code), and talks only to Google's APIs. Its author operates no
server and receives no data from it.

## Data it accesses

With the `https://www.googleapis.com/auth/gmail.modify` scope (Gmail) and the Calendar scopes
`calendar.calendarlist.readonly`, `calendar.events.readonly`, `calendar.events.owned` and
`calendar.events.freebusy` (Google Calendar), and only when your MCP client calls one of its
tools, the server reads:

- message metadata: sender, recipients, subject, date, labels, size and snippet;
- message bodies, when you ask for a message or a thread;
- your labels and their counts;
- your calendars and their time zones;
- events: title, times, description, location, conference link, and the names and email
  addresses of the organizer and guests;
- busy intervals (free/busy) of the calendars you name;
- your account's email address, to check that the authorized account is the configured one.

When you ask for it, it also:

- adds or removes labels, moves messages to the trash and restores them;
- creates labels;
- downloads an attachment you name;
- creates, changes and deletes events on calendars you own, only for events that have no other
  guests and are organized by you. It adds no guests and sends no invitations or notifications.

It never sends email, never deletes messages permanently and never changes account settings.

## How the data is used

The server returns what a tool call asked for to the MCP client that made the call. What the
client does with it, including sending it to an AI model, is governed by that client's own
terms and privacy policy.

## What is stored, and where

Everything stays on your computer:

- the OAuth token, in `~/.config/google-personal-mcp/token.json` with file mode 600;
- attachments, only when you ask for one, in `~/Downloads/google-personal-mcp`.

The server writes no email or calendar content to disk. After each write it logs the changed message or event IDs
to its standard error stream, which your MCP client may keep in its local logs. Google's
client library may also log request URLs there, and a URL can include a search query or an event ID.

## Sharing

No data is sent to the author or to any third party. The only network traffic is between your
computer and Google's APIs. There is no telemetry, no advertising and no sale of data.

google-personal-mcp's use and transfer of information received from Google APIs adheres to
the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Revoking access

- Remove the app's access at <https://myaccount.google.com/permissions>.
- Delete `~/.config/google-personal-mcp/token.json`.
- Delete any attachments you no longer want from the download folder.

## Changes and contact

Changes to this policy are made in this file and recorded in the repository's history.
Questions go to the repository's issue tracker:
<https://github.com/slackwero/google-personal-mcp/issues>.
