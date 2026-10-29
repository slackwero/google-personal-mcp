# google-personal-mcp

A local MCP server (stdio) that lets Claude Code read and organize the Gmail mailbox of **one
personal Google account**, using Google's public Gmail API and an OAuth client you create
yourself. Main use: cleaning a cluttered mailbox by finding old, unimportant and heavy
emails, labeling them and moving them to the trash. Calendar support is planned.

There is no sending, replying or permanent deletion. The only scope requested is
`https://www.googleapis.com/auth/gmail.modify`.

## Tools

| Kind | Tools |
|---|---|
| Read | `gmail_search`, `gmail_get_message`, `gmail_get_thread`, `gmail_list_labels`, `gmail_top_senders` |
| Write | `gmail_create_label`, `gmail_modify_labels`, `gmail_trash`, `gmail_untrash`, `gmail_download_attachment` |

Bulk write tools take either `message_ids` (1 to 1,000) or `query` plus `max_messages`
(1 to 1,000). `gmail_modify_labels` rejects the `TRASH`, `SPAM`, `SENT` and `DRAFT` labels.

## Google Cloud setup

Do this once, signed in with the Google account the server will manage.

1. Create a Google Cloud project in the web console (no organization or billing account needed).
2. Enable the **Gmail API**.
3. In **Google Auth Platform**, configure the consent screen as **External** and add your
   account as a test user.
4. Create an OAuth client of type **Desktop app** and download its JSON right away: the
   console shows the client secret only once.
5. Save it in the config directory (default `~/.config/google-personal-mcp`, mode 700) with file mode 600,
   under the file name the `login` error message names if it is missing.

Gmail API use is free of charge for personal use.

## Install and log in

```bash
cd <path to this repo>
uv sync
GOOGLE_PERSONAL_MCP_ACCOUNT=<your Gmail address> uv run google-personal-mcp login
```

`login` opens the browser, checks that the authorized account matches
`GOOGLE_PERSONAL_MCP_ACCOUNT`, and stores the token in the config directory with mode 600.
Exit codes: `0` ok, `1` authentication error, `2` configuration error.

While the OAuth app is in **Testing**, the refresh token expires after **7 days**: run
`login` again when the server reports an expired or revoked token.

## Register with Claude Code

Run this from the folder where you will use the MCP:

```bash
claude mcp add --scope local google-personal \
  -e GOOGLE_PERSONAL_MCP_ACCOUNT=<your Gmail address> \
  -- uv run --directory <path to this repo> google-personal-mcp serve
```

Always use `--scope local` (or `project`), never `--scope user`: a user-scoped server would
expose your mailbox in every other project's sessions.

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
      "mcp__google-personal__gmail_top_senders"
    ],
    "ask": [
      "mcp__google-personal__gmail_create_label",
      "mcp__google-personal__gmail_modify_labels",
      "mcp__google-personal__gmail_trash",
      "mcp__google-personal__gmail_untrash",
      "mcp__google-personal__gmail_download_attachment"
    ]
  }
}
```

The `ask` rules are needed even though the write tools are absent from `allow`: an `ask` rule
still prompts in auto mode and after a mistaken "don't ask again".

**Warning:** `bypassPermissions` mode skips every prompt. Do not use this MCP in that mode.

## Undo

Every write result returns the IDs it changed. Trashed mail stays in Gmail's trash for 30 days.
To undo a trash exactly, pass the `inbox_ids` returned by `gmail_trash` to `gmail_untrash` as
`restore_inbox`: Gmail does not put restored mail back in the inbox on its own.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_PERSONAL_MCP_ACCOUNT` | none (required) | The Gmail address this server manages. |
| `GOOGLE_PERSONAL_MCP_MODULES` | `gmail` | Comma-separated modules to enable. |
| `GOOGLE_PERSONAL_MCP_CONFIG_DIR` | `~/.config/google-personal-mcp` | Holds the OAuth client file and the token. |
| `GOOGLE_PERSONAL_MCP_DOWNLOAD_DIR` | `~/Downloads/google-personal-mcp` | Where attachments are saved. |

## Security

- Email content (subjects, senders, bodies, attachment names) is untrusted input and may
  contain prompt injection. Tools return it as data.
- No secrets live in this repo. The client secret and the token stay in the config directory;
  the token file has mode 600.
- Attachments are downloaded only on explicit request, only into the download directory
  (created with mode 0700), with sanitized names, and an existing file is never overwritten.
