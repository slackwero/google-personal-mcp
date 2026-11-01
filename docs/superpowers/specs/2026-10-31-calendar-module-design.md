# Calendar module: design

- **Date:** 2026-10-31
- **Status:** approved by the user on 2026-10-31. The user asked to skip the questions, so the
  intent and every choice not already recorded in `CLAUDE.md` are design calls, marked
  *(design call)*.
- **Scope:** the Calendar module of `google-personal-mcp` (work plan step 9).

Project-wide context lives in `CLAUDE.md`, and the Gmail module's design
(`2026-10-29-gmail-module-design.md`) sets the conventions this module reuses: stack, package
layout, configuration, token handling, error mapping, the approval rule and the test approach.
This document only covers what Calendar adds or changes. Google API facts below were read from
developers.google.com/workspace/calendar and support.google.com/calendar on 2026-10-31.

## Intent

*(design call)* The user wants Claude Code to work with the Google Calendar of the same
personal account they already manage through the Gmail module: see what is coming, find free
time, and create, move or cancel their own events, often from something in their mail (a
booking confirmation, an appointment).

Success looks like: in a Claude Code session the user asks "what do I have this week?", "when
am I free on Thursday afternoon?" or "add the dentist appointment from that email", sees the
whole event in the approval prompt, approves, and can undo it.

## Goals

- List the calendars the account sees (own, shared and subscribed) with their access role and
  time zone.
- List the events in a time range on one calendar, with recurring events expanded into
  occurrences, optionally filtered by free text.
- Read one event in full.
- Ask for free/busy time across one or more calendars.
- Create an event on a calendar the account owns: timed or all-day, with an optional
  description, location and recurrence.
- Change and delete the account's own events, with what is needed to undo it in the result.

## Non-goals (first version)

- **Guests.** No adding attendees, no invitations, updates or cancellations sent to anyone, no
  replies to invitations. Events that have guests other than the account are read-only (see
  "Write guards").
- Calendar management: creating or deleting calendars, sharing, access rules, subscribing.
- Settings, colors, reminders, attachments and video-call links.
- Special event types (out of office, focus time, working location, birthdays, events from
  Gmail): read-only.
- Writing to calendars the account does not own, even where it has write access.
- Unattended or scheduled runs; multiple accounts (as for Gmail).

## Anti-goals (failure even if it technically works)

- An event is created, changed or deleted without the user approving that call.
- Someone else's calendar changes, or someone gets an email, invitation, update or
  cancellation, because of this server.
- Text inside an event steers Claude into an action. Anyone can put an event on this calendar
  by sending an invitation, so titles, descriptions and locations are third-party text, like
  email.
- A whole recurring series changes or disappears when the user meant one occurrence.
- Turning the Calendar module on breaks the Gmail tools.
- The Calendar write tools appear in a session before their `ask` rules exist.
- The token belongs to the wrong account, a secret reaches the public repo, or the server is
  reachable from other projects (as for Gmail).

## Constraints

- **Stack, language, public repo, stdio, approval rule:** as in the Gmail design.
- **Scopes** *(design call, confirming the note in `CLAUDE.md`)*. Each method accepts any one
  of the scopes listed on its reference page. The smallest set that covers the goals:

  | Scope (`https://www.googleapis.com/auth/…`) | Google's description | Needed for |
  |---|---|---|
  | `calendar.calendarlist.readonly` | "See the list of Google calendars you're subscribed to." | `calendarList.list` and `calendarList.get`, which also give each calendar's time zone and the account check |
  | `calendar.events.readonly` | "View events on all your calendars." | `events.list`, `events.get` and `events.instances` on own and shared calendars |
  | `calendar.events.owned` | "See, create, change, and delete events on Google calendars you own." | `events.insert`, `events.patch` and `events.delete` |
  | `calendar.events.freebusy` | "See the availability on Google calendars you have access to." | `freebusy.query`, which does not accept `calendar.events.readonly` |

  `CLAUDE.md` proposed `calendar.events`. `calendar.events.owned` is narrower: it cannot write to
  calendars that others share with the account, which is a non-goal anyway.
  `calendar.events.owned.readonly` would not be enough for reading shared calendars.
- **Quota** (developers.google.com/workspace/calendar/api/guides/quota): **600 requests per
  minute per user** and 10,000 per minute per project, in a sliding window. Rate-limit errors
  are 403 (`rateLimitExceeded`, `userRateLimitExceeded`, `quotaExceeded`) and 429; Google
  says to treat both with exponential backoff.
- **Time** (events resource reference):
  - A timed event has `dateTime` plus an optional IANA `timeZone`; a `dateTime` without an
    offset needs the `timeZone`.
  - An all-day event has `date`, and its `end` is **exclusive**: a one-day event on 3 November
    ends on 4 November.
  - Mixing the two forms in one event is invalid.
  - A recurring event must carry a `timeZone`.
- **Event types** (events resource reference): `default`, `birthday`, `focusTime`, `fromGmail`,
  `outOfOffice` and `workingLocation`. The type cannot change after creation, and `fromGmail`
  events cannot be created.

## Decided earlier (`CLAUDE.md`)

- One server with modules enabled by configuration; the login requests only the enabled
  modules' scopes.
- Tool prefix `calendar_*`, so the tools are never confused with the claude.ai Google Calendar
  connector, which points to another account.
- Events only: no whole-calendar management or sharing.
- The tool list: `calendar_list_calendars`, `calendar_list_events`, `calendar_get_event`,
  `calendar_freebusy`, `calendar_create_event`, `calendar_update_event` and
  `calendar_delete_event`.

## Design

### Package

*(design call)* A package `google_personal_mcp/gcalendar/` with the same split as Gmail
(`client.py`, event parsing and time handling, write guards, `tools.py`). It is not named
`calendar` so that it never shadows the standard library's `calendar` module, which
`email.utils` and `http.cookiejar` import. The module's configuration name is still
`calendar`.

### Configuration

- `KNOWN_MODULES` gains `calendar`.
- *(design call)* When `GOOGLE_PERSONAL_MCP_MODULES` is unset, every known module is enabled,
  which is today's behavior, so the default becomes `gmail,calendar`. The existing
  registration needs no change. Turning Calendar on in the code therefore turns it on in
  `personal-manager`, which is why the `ask` rules come first (see "Rollout order").
- The README's configuration table changes its default accordingly. `GOOGLE_PERSONAL_MCP_MODULES=gmail`
  keeps a Gmail-only server.

### Authentication

- `MODULE_SCOPES["calendar"]` holds the four scopes above. `login` requests the scopes of all
  enabled modules, as today.
- **The scope check is per module** *(design call)*. Today `load_credentials` checks the scopes
  of every enabled module, so a token from before Calendar was enabled would make the Gmail
  tools fail too. Instead, each module's tools check only that module's scopes. With an old
  token, Gmail keeps working, and the Calendar tools answer that the token lacks Calendar
  access and a new `login` is needed. The next login (due every 7 days anyway while the app is
  in "Testing") grants both.
- **Partial consent.** Google's consent screen can let the user untick individual scopes.
  `login` saves the token and prints which modules lack a scope, instead of failing. The
  builder must check two library behaviors:
  - oauthlib raises "Scope has changed" when Google grants fewer scopes than requested, unless
    `OAUTHLIB_RELAX_TOKEN_SCOPE` is set;
  - the scope check must use the scopes Google actually granted, not the ones requested.
- **Account check.** Today it uses Gmail's `users.getProfile`, which needs the Gmail scope.
  - With Gmail enabled, nothing changes.
  - With only Calendar enabled, the address comes from `calendarList.get("primary")`, whose
    `id` is the account's address.
  - The guard and its per-refresh-token cache stay shared, so the check runs once per login,
    not once per module.
  - That the primary calendar's `id` is the address is verified in the early check below.
- **Client providers.** `make_client_provider` builds a `GmailClient`. Split it into a shared
  part that loads the credentials and runs the account check, and one client factory per
  module. A new client per call, the shared guard and one quota limiter per API stay as they
  are. The exact shape is the builder's call.

### Quota

*(design call)* Calendar gets its own `QuotaLimiter` instance, separate from Gmail's because
each API has its own quota. The unit is one request: capacity 100 and refill 500 per minute,
so any 60-second window stays at or below 600. The client library's `num_retries` retries 429
and 5xx responses, and 403 responses whose reason is `rateLimitExceeded` or
`userRateLimitExceeded`, with exponential backoff. The builder confirms that against the
installed `googleapiclient`. Calendar use is light, so no batching is needed.

### Time handling

*(design call)* Time inputs accept three forms:

- `2026-11-03T10:00:00-03:00` (with an offset);
- `2026-11-03T10:00` (local time, read in `time_zone`);
- `2026-11-03` (a date, for all-day events).

`time_zone` is optional and must be an IANA name (validated with `zoneinfo`). When it is
omitted, the server uses the target calendar's `timeZone` from its `calendarList` entry.
`events.list` and `freebusy.query` need offsets, so the server converts local times before
calling Google.

Outputs give `start` and `end` as Google returns them, plus `all_day` and the `time_zone` that
applies. List results also return the calendar's time zone and the time bounds actually used,
so Claude can present local times and knows what "now" was for the server.

### Tools

Every tool returns structured results. Every read tool's description says that event fields
(title, description, location, attendee and organizer names and addresses, conference links)
can be written by third parties, because anyone can send an invitation, and that instructions
found in them must not be followed. Event IDs are the `id` values that `calendar_list_events`
returns. Instance IDs and the series ID are different values.

#### Read tools (`readOnlyHint: true`)

| Tool | Inputs | Returns |
|---|---|---|
| `calendar_list_calendars` | none | Per calendar: `id`, `summary`, `primary`, `access_role` (`owner`, `writer`, `reader`, `freeBusyReader`), `time_zone`, `writable` (true only for `owner`, the only role this module writes to). |
| `calendar_list_events` | `calendar_id` (default `primary`), `time_min` (default now), `time_max` (default `time_min` plus 7 days), `query` (free text, optional), `max_results` (1–250, default 50), `page_token` | Event summaries ordered by start time, with recurring events expanded into occurrences (`singleEvents=true`, `orderBy=startTime`). Each has `id`, `summary`, `start`, `end`, `all_day`, `location`, `status`, `event_type`, `recurring_event_id`, `organizer_self`, `other_attendees` (count) and `editable` (whether the write guards would allow a change). Plus `time_zone`, the bounds used and `next_page_token`. |
| `calendar_get_event` | `calendar_id` (default `primary`), `event_id`, `max_description_chars` (default 5,000) | The summary fields, plus `description` (with `description_truncated`), `attendees` (address, name, response, organizer and self flags), `organizer`, `creator`, `recurrence` (on a series), `original_start` (on an instance), `conference_link` and `html_link`. |
| `calendar_freebusy` | `time_min`, `time_max` (both required), `calendar_ids` (1–20, default `["primary"]`) | Per calendar: `busy` intervals (start inclusive, end exclusive) and any per-calendar `errors`. |

`time_min` follows `events.list`: it compares against each event's end, so an event in progress
is included, and `time_max` excludes events that start at or after it. The free-text search
covers titles, descriptions, locations and attendee and organizer names and addresses.

#### Write tools (approval every time)

Every write passes `sendUpdates=none` and never sends attendees. Since the guards keep the
module away from events with other guests, no one else is affected.

| Tool | Inputs | Behavior |
|---|---|---|
| `calendar_create_event` | `calendar_id` (default `primary`), `summary` (required), `start` (required), `end` (required for timed events; optional for all-day events, defaulting to the next day), `time_zone`, `description`, `location`, `recurrence` (RFC 5545 lines such as `RRULE:FREQ=WEEKLY;COUNT=10`) | Creates a `default` event with no attendees. A recurring event always gets a `time_zone`, from the calendar when none is given. Returns the created event in full. |
| `calendar_update_event` | `calendar_id`, `event_id`, `series` (default false), and any of `summary`, `start`, `end`, `time_zone`, `description`, `location`, `recurrence` | Patches only the given fields; an empty string clears `description` or `location`. If only `start` is given, `end` moves with it and the duration stays the same. Returns `before` (the previous values of the changed fields) and the event after the change. |
| `calendar_delete_event` | `calendar_id`, `event_id`, `series` (default false) | Deletes the event. Returns the deleted event in full (including `recurrence`) so it can be re-created. `destructiveHint: true`. |

#### Write guards

Before an update or a delete, the server reads the event (one `events.get`) and the calendar's
`calendarList` entry, and refuses with a clear message unless all of these hold:

- The calendar's `access_role` is `owner`, which is what `calendar.events.owned` covers.
- `event_type` is `default`.
- The event has no attendees other than the account itself. Google's help says an organizer's
  delete "takes the event off your calendar, and off the calendars of everyone else invited",
  and a guest's delete tells the organizer they declined (support.google.com/calendar/answer/37113).
  The API reference does not say whether `sendUpdates=none` changes that, so the module stays
  away from such events altogether.
- The event is not `cancelled` and not `locked`.
- **Series and occurrences** *(design call)*:
  - An event with a `recurrence` is a whole series. Changing or deleting it requires
    `series: true`, so the approval prompt shows that the whole series is affected.
  - An occurrence (an ID with a `recurring_event_id`) is changed or deleted alone, and
    `series: true` with an occurrence ID is an error.
  - `recurrence` can only be set on a series or on a non-recurring event.

`calendar_create_event` checks only the calendar's role. It has no attendee or event-type
input, so the other guards cannot fail on creation.

#### Undo

- **Create:** delete the returned `id`.
- **Update:** call `calendar_update_event` again with the `before` values.
- **Delete:** re-create the event from the returned snapshot with `calendar_create_event`.
  - The new event gets a new ID.
  - A re-created series loses any occurrences that had been edited one by one.
  - Google Calendar also keeps deleted events in that calendar's trash for 30 days,
    restorable from the web settings, except deletes of "this and following events".
  - The API has no method to restore a deleted event.
- Each successful write logs the tool name and the event ID, never titles, descriptions or
  times, to stderr, as the Gmail writes do.

### Verify early (before the write tools are built)

*(design call)* As with Gmail's trash round trip, this runs once against the real calendar,
after a login with the Calendar scopes and before the write tools exist. It uses only events it
creates itself, with a title marked as a test:

1. Check that `calendarList.get("primary").id` equals the configured account address.
2. Create a daily test event with three occurrences on the primary calendar.
3. Delete one occurrence by its instance ID, then check that the other two remain.
4. Patch one occurrence's time, then check that only that one moves.
5. Delete the series by its own ID, then check that no occurrence remains.
6. Record what `events.get` returns for the deleted event.

The reference pages do not state what a delete or patch on a series ID does versus an instance
ID; the guards above assume the behavior the recurring-events guide implies. If the check
shows otherwise, the guards change before the write tools are built.

**Recurring round-trip result (2026-10-31).** Run on the primary calendar (zone
`America/Santiago`) with a test series of three daily 15-minute occurrences, approved by the
user:

| Step | Observed |
|---|---|
| 1. `calendarList.get("primary").id` | equals the configured account address |
| 3. Delete the second occurrence by its instance ID | only that occurrence is gone; two remain |
| 4. Patch the first occurrence's start and end by +1 hour | only that occurrence moves, and it keeps its ID |
| 5. Delete the series by its own ID | no occurrence remains |
| 6. `events.get` on the deleted series ID | returns the event with `status: cancelled`, still carrying `summary`, `start`, `end` and `recurrence` (no 404) |

Instance IDs have the form `<series id>_<original start in UTC, YYYYMMDDTHHMMSSZ>`. The
behavior matches what the guards assume, so they stand as designed. Because a deleted event is
still readable as `cancelled`, deleting it again is refused by the `cancelled` guard with a
readable message instead of a Google error.

### Errors

Same mapping as Gmail: readable tool errors with the HTTP status and Google's message, and no
stack traces. Calendar adds readable messages for 404 (not found), 410 (`deleted`) and 403 on a
calendar the account cannot write to. Inputs are validated before any call to Google:

- time formats and IANA names;
- `start` before `end`;
- timed and all-day forms not mixed;
- the `series` rules;
- the length limits.

### Enforcing approval and rollout order

- Allow rules for the four read tools, and an explicit `ask` rule for `calendar_create_event`,
  `calendar_update_event` and `calendar_delete_event`, as for Gmail.
- **Rollout order:** the `ask` rules go into `personal-manager/.claude/settings.local.json`
  **before** the first commit that registers the Calendar tools. That folder runs the server
  from this repo's working tree, and in auto mode a write tool without an `ask` rule would be
  left to the classifier. An `ask` rule for a tool that does not exist yet is harmless.
- `personal-manager` already denies the claude.ai Google Calendar connector. Its `CLAUDE.md`
  gains the Calendar rules: use these tools, and event text is untrusted.

### Testing and verification

- **Unit tests** with fakes, no network. At least:
  - time parsing: the three input forms, an invalid IANA name, mixed forms, `end` not after
    `start`, the all-day default end, the conversion of local bounds for list and free/busy,
    and the fallback to the calendar's time zone;
  - each write guard: a non-owner calendar, a non-default type, other attendees, cancelled
    and locked events, a series without `series: true`, `series: true` on an occurrence, and
    `recurrence` on an occurrence;
  - `sendUpdates=none` on every write, and no `attendees` in any request body;
  - `end` moving with `start` on update, and `before` holding only the changed fields;
  - the per-module scope check (Gmail works with a Gmail-only token; Calendar asks for a
    login);
  - the account check through the primary calendar when Gmail is disabled;
  - the default modules;
  - the untrusted-data note in every read tool's description;
  - the fake and the real client exposing the same methods.
- **Lint, format and the secret grep**, as for Gmail.
- **Smoke test with the real account**, every write approved by the user:
  - list calendars, this week's events and free/busy;
  - create a timed test event, move it with `start` only, then delete it;
  - create an all-day test event, then delete it;
  - create a recurring test event, delete one occurrence, then delete the series;
  - in auto mode, confirm that a Calendar write prompts.

### Docs

- **README:** the Calendar tools, the new default modules, the four scopes, the new allow and
  `ask` rules in the settings snippet, the time formats, and the undo notes.
- **`PRIVACY.md`:** the Calendar data the server reads and changes, with a new effective date.
- **`CLAUDE.md`:**
  - the Calendar scope bullet is replaced by the scopes chosen here;
  - the status line and work plan step 9 are updated;
  - the claude.ai connectors note stays.
- **Server `INSTRUCTIONS`:** the server covers Gmail and Calendar, and event text is untrusted.

## Left to the builder

- The exact module layout, models and the shape of the client-provider split.
- Description and summary length limits (Google's own limits apply anyway).
- Whether `calendar_freebusy` caps the time range or relies on Google's error.
- Confirming the library behaviors named above: `num_retries` on 403 rate limits, oauthlib's
  scope-change error, and the granted versus requested scopes.

**Answers (2026-10-31, from the code as built):**

- *Module layout.* `src/google_personal_mcp/gcalendar/` (named to avoid shadowing the
  standard library) holds `client.py` (thin API client with quota accounting, retries and
  readable errors), `events.py` (event models and parsing), `guards.py` (pure write checks),
  `operations.py` (create, update, delete with the `before` and snapshot results), `times.py`
  (time parsing) and `tools.py` (MCP registration). `server.make_client_providers` returns one
  provider per module; each builds a new client from freshly loaded credentials on every call
  and requires only its own module's scopes. The quota limiter is per module, and one account
  check is shared and cached per refresh token.
- *Length limits.* `summary` 1 to 1,024 characters, `description` up to 8,192, `location` up to
  1,024, `calendar_ids` 1 to 20, `max_results` 1 to 250. Google's own limits still apply.
- *Free/busy range.* `calendar_freebusy` does not cap the time range; it only checks that
  `time_max` is after `time_min` and relies on Google's error for anything else.
- *Library behaviors.* googleapiclient's `execute(num_retries=...)` retries 403
  `rateLimitExceeded` and `userRateLimitExceeded` and 429, which is why the client passes it.
  oauthlib raises "Scope has changed" when Google grants fewer scopes than requested unless
  `OAUTHLIB_RELAX_TOKEN_SCOPE` is set, which `login` does. `to_json` records the requested
  scopes, so `login` rewrites the token to record the scopes Google actually granted, and the
  per-module scope check uses those.
