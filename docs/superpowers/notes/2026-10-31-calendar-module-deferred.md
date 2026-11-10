# Calendar module: review notes left for later

The reviews of the Calendar module (plan `docs/superpowers/plans/2026-10-31-calendar-module.md`)
recorded these minor findings and chose not to fix them before merging. None of them changes
what a write tool allows. They are kept here so they are not lost; each one is worth a look the
next time that area changes.

The items under "Still open" were recorded during the reviews and were not re-checked one by
one against the current code.

## Still open

### Code

- `_to_api_error` is duplicated in `gmail/client.py` and `gcalendar/client.py`; it could become
  `GoogleApiError.from_http_error`.
- `cli.py` hardcodes the `calendarlist.readonly` scope URL, and `cli.py` and `server.py` both
  build the Calendar limiter.
- `shift_end` keeps an absolute duration but adds it to a naive new start as wall-clock time,
  so across a daylight saving change the end can be off by an hour.
- A `dateTime` without an offset is localized in the calendar's zone, not in the event's
  `timeZone`.
- `zone()` raises `ValueError("empty")` inside its own `try`.
- `all_day` is true for a start of `{"date": None}`.
- A negative `max_description_chars` slices from the end.
- `other_attendees` and the write guard count non-dict attendees differently.
- `attendees: None` blocks an edit (a side effect of failing closed on unreadable guest lists;
  Google omits the key when the list is empty, so this is not seen in practice).
- Default time bounds are echoed in `+00:00`, while explicit ones use the calendar's offset.
- Helper placement in `gcalendar/tools.py` is inconsistent.
- Smells: a `type: ignore` in `operations.py`; `parse_google_time` drops the exception chain;
  `DEFAULT_ZONE` is defined twice; the `_event_input` call is duplicated; the `8_192` limit
  appears twice as a literal.

### Undo and updates

- `before` cannot always be passed back as is: an untitled event gives `summary: ""`, which
  fails `min_length=1`, and a description longer than 8,192 characters fails the tool's limit.
- The delete snapshot keeps up to 100,000 description characters, but create accepts 8,192
  (the plan asked for this; the README now says so).
- A recurrence-only update on an event whose start has no `timeZone` gets a 400 from Google;
  the update could add the calendar's zone.
- `before["time_zone"]` comes from `start` only and is left out when `start` has none; an
  end-only update with `time_zone` restores the end's zone from the start's.
- Undoing an update that added a recurrence needs `series: true`, which `before` does not carry;
  the update description could say so.

### Tests

- `run_login` sets `OAUTHLIB_RELAX_TOKEN_SCOPE` for the whole process, and five `run_login`
  tests do not use `monkeypatch`, so it leaks between tests; an autouse `delenv` fixture in
  `test_auth.py` would fix it.
- The Calendar limiter's settings (500 per minute, capacity 100) and its sharing across Calendar
  calls are not asserted.
- A test in `test_auth.py` compares against `scopes_for(...)`, which is circular; it should pin
  the literal list.
- `test_login_with_no_module_granted_saves_nothing` matches only the login hint.
- In `test_cli.py`, `ENV_EMAIL` and `_gmail_only_credentials` are defined after use and
  duplicate existing helpers.
- The fake Calendar client's `get_event` and `patch_event` return the stored dict, so a caller
  can change the fake's state; `add_calendar(primary=True)` with a non-primary ID creates two
  primary entries; and `patch_event` does not model Google's merge semantics.
- Missing tests: `shift_end` across a daylight saving change, `parse_time` with a non-string,
  `max_description_chars=0`, a time bound with an explicit offset, a calendar entry without
  `timeZone` (UTC fallback), the full set of registered tools, `idempotent_hint` false on create
  and update, several description phrases, the create log line, and a tool-level round trip
  that switches an event between timed and all-day.

### Docs

- `quota.py` has a short docstring line.
- `PRIVACY.md` could say "regular events".

## Kept as they are, by decision

- After a retried insert gets a 409, a failed read-back re-raises the 409, so a create that
  did happen can be reported as failed. It is never reported as a false success, and it needs
  both a retried insert and a failed read.
- `locked` blocks a write only when it is `true`, while `attendeesOmitted` blocks on any value
  other than `false`. The difference is harmless.

## Fixed after they were recorded

- The login printed the redirect URL with the one-time authorization code; that log line is now
  silenced.
- A test now checks that `fetch_email` receives the rebuilt credentials with the granted scopes.
- `shift_end` raised `KeyError` or `TypeError` on a malformed event; it now raises `ValueError`.
- The tests for a reversed time range now prove that no call reached Google.
- The write tool descriptions now carry the untrusted-content note.
- `editable` on a series reflects only the main event; the README and the spec now say so.
- `CLAUDE.md` and `PRIVACY.md` wording and line wrapping.
