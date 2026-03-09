# telegram-nmbot

This directory contains a small maintenance workflow for updating an nmBot keyword-reply template.

The current template in this directory is a user-facing Telegram auto-reply for the subscription-source keyword flow. It is not developer documentation.

## Files

### `auto-message.txt`

This is the single source of truth for the bot reply text.

What it is:

- A Telegram bot auto-reply message
- Written for end users in the Syncnext Telegram group
- Intended to explain how to use subscription sources inside the app

What it is not:

- Not internal engineering documentation
- Not a full source-of-truth database
- Not a schema or API contract

Audience:

- Existing Syncnext users
- New users asking how subscription sources work
- Telegram group members who need a copy-paste style reply

Content style:

- User-facing
- Direct and operational
- Plain text only
- Written as final reply text, not as notes for maintainers

Rules:

- Use plain text only
- Do not use Markdown formatting
- Write the text exactly as it should appear in Telegram
- Line breaks and blank lines are preserved

### Where the data comes from

`auto-message.txt` is maintained from a mix of verifiable project data and operator-maintained group information.

Usually verifiable from the workspace:

- `SyncnextAPI/sourcesv3.json`
- `SyncnextAPI/sources18.json`
- `SyncnextAPI/source_ali.json`
- `syncnextPlugin_icyl/sourcesv3-lite.json`
- `syncnextPlugin_icyl/sourcesv3-18x.json`
- `syncnextPlugin_icyl/sourcesv3.json`
- `SyncnextPlugin_nqaq/sourcesv3_qoli.json`
- App behavior or wording inside `Syncnext/` when relevant

May require manual confirmation from the Telegram group or maintainers:

- Human-facing short codes or “subscription passwords”
- Group-specific operating instructions
- Third-party source distribution details
- Any wording that depends on current group policy rather than repository state

Maintenance rule:

- If a line can be verified from repository files, verify it before editing
- If a line is group-operations information, keep it clearly user-facing and confirm it from current practice
- Do not present guesses as confirmed facts

### `config.json`

This file contains the operational target for the update script.

It currently defines:

- `chatId`
- `templateId`
- `title`
- `keyboard`

Use this file when:

- the target template changes
- the target Telegram group changes
- the inline buttons need to be updated

Do not edit Python constants for these values unless the config format itself needs to change.

### `edit-bot-message.py`

This script performs two steps:

1. Connect to an already logged-in Arc browser session through `playwright-cli`
2. Read the current nmBot `session_id` from browser storage and submit the template update via Python HTTP

### `playwright-arc-cdp-local-9222.json`

Local Playwright config for connecting to Arc CDP on `http://127.0.0.1:9222`.

## Prerequisites

Before running an update, make sure:

1. `python3` is installed
2. `playwright-cli` is installed
3. Arc is running with CDP enabled
4. Arc is logged in to `https://nmbot.nmnm.fun/`
5. The nmBot group management page is accessible

If Arc is not already running with CDP enabled:

```bash
open -a "Arc" --args --remote-debugging-port=9222 --remote-allow-origins='*'
```

## Daily Workflow

### 1. Update the content carefully

Update:

- `telegram-nmbot/auto-message.txt`

Recommendations:

- Keep the message written for Telegram group users, not maintainers
- Avoid Markdown syntax
- Use full URLs if a link should be visible
- Prefer short operational wording over explanations about repository internals
- Re-check dates, source lists, and URLs before pushing
- If the message includes counts or lists of channels, verify them against the current JSON files
- If the message includes group workflow instructions, confirm they still match current moderator practice
- Re-read the final text once before pushing

### 1.5. Update operational config only when needed

Update `telegram-nmbot/config.json` only if one of these changes:

- target `chatId`
- target `templateId`
- template `title`
- inline keyboard buttons

If the task is only a wording/content update, you usually do not need to touch `config.json`.

## Maintenance Checklist

Before pushing an update to the bot, go through this checklist:

1. Confirm the task is user-facing Telegram reply maintenance, not developer documentation work.
2. Edit `auto-message.txt` only for reply text changes.
3. Verify repository-backed facts against current files:
   - `SyncnextAPI/sourcesv3.json`
   - `SyncnextAPI/sources18.json`
   - `SyncnextAPI/source_ali.json`
   - `SyncnextPlugin_nqaq/sourcesv3_qoli.json`
   - `syncnextPlugin_icyl/sourcesv3-lite.json`
   - `syncnextPlugin_icyl/sourcesv3-18x.json`
   - `syncnextPlugin_icyl/sourcesv3.json`
4. Confirm any group-operation wording with current moderator or operator practice.
5. Check dates, URLs, short codes, and button destinations.
6. If needed, update `config.json`.
7. Run `--dry-run`.
8. Review the dry-run summary carefully.
9. Submit the update.
10. Verify that the API response reports success for the expected template.

### 2. Run a dry run first

From the workspace root:

```bash
python3 telegram-nmbot/edit-bot-message.py --dry-run
```

This does not update the remote template. It only verifies:

- `auto-message.txt` can be loaded
- `config.json` can be loaded
- Arc / CDP is reachable
- nmBot is logged in
- a usable `session_id` is available

### 3. Submit the update

After a successful dry run:

```bash
python3 telegram-nmbot/edit-bot-message.py
```

Success should include:

- `"ok": true`
- `"status": 200`

## Default Target

Default target settings now come from:

- `telegram-nmbot/config.json`

You can override them from the command line:

```bash
python3 telegram-nmbot/edit-bot-message.py --template-id 12345 --chat-id -1000000000000
```

## Common Commands

### Syntax check

```bash
python3 -m py_compile telegram-nmbot/edit-bot-message.py
```

### Use a different text file

```bash
python3 telegram-nmbot/edit-bot-message.py --message-file /path/to/other.txt
```

### Use a different config file

```bash
python3 telegram-nmbot/edit-bot-message.py --config-file /path/to/config.json
```

### Keep the Playwright session open

```bash
python3 telegram-nmbot/edit-bot-message.py --keep-open
```

## Troubleshooting

### No logged-in session found

Common causes:

- Arc is not logged in to nmBot
- Arc was not started with CDP enabled
- the script is pointing at the wrong browser session

Fix:

1. Open `https://nmbot.nmnm.fun/` in Arc
2. Confirm the account is logged in
3. Retry the dry run

### API is not returning 200

Common causes:

- the browser `session_id` expired
- the target template does not exist
- permission was lost
- the website is temporarily failing

Fix:

1. Re-open the nmBot panel in Arc
2. Run `--dry-run` again
3. Submit again

### The pushed text looks wrong

Check:

- extra blank lines
- accidental Markdown markers
- wrong URLs, passwords, or group commands

## Maintenance Notes

- Only edit `auto-message.txt` for message content changes
- Edit `config.json` only for target/template/button changes
- Do not hardcode session tokens into scripts
- Always run `--dry-run` before a real update
- Keep local machine-specific overrides out of version control
- Treat `auto-message.txt` as a user support artifact, not a developer note
- When repository facts and Telegram operational practice diverge, resolve that intentionally before publishing
