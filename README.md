# SyncnextTelegram nmBot maintenance

This repository maintains every nmBot keyword auto-reply for the Syncnext Telegram group.

nmBot stores an auto-reply in two connected layers:

- `keywordReplies`: matching rules, enablement, cooldowns, moderation actions, and `replyMessageIds`
- `messageTemplates`: text, media references, inline keyboards, and link-preview settings

Both layers are tracked together. A keyword rule is not complete unless every ID in its
`replyMessageIds` exists in `messageTemplates`.

## Tracked files

- `config.json`: group, panel, API, and local-state locations; no login token
- `nmbot-state.json`: all keyword rules and message-template metadata
- `replies/template-<id>.txt`: plain-text bodies created by `pull`
- `nmbot_maintain.py`: pull, validate, compare, and targeted-update tool
- `playwright-arc-cdp-local-9222.json`: connection to the existing Arc CDP session

`nmbot-state.json` must have `snapshot.complete: true` before it is treated as the maintenance
baseline. The tracked state was bootstrapped with a complete nmBot read on 2026-08-02.

## Safety contract

- `validate` is fully local.
- `pull` reads nmBot and updates local tracked files; it never changes nmBot.
- `diff` reads nmBot and reports ID-level differences; exit code `2` means differences exist.
- `apply` targets exactly one existing ID.
- `apply` does not mutate nmBot unless `--confirm` is present.
- `delete` targets exactly one object that has already been removed from valid local state.
- `delete` refuses to remove a message template while any local or remote rule references it.
- Add is deliberately not implicit. A missing local or remote ID is an error during updates.
- After an update, the tool reads the object back and fails if nmBot differs from local state.
- Browser session IDs are read from the logged-in Arc session and are never written to the repo.

## Prerequisites

1. `python3` and `playwright-cli` are installed.
2. Arc CDP is available at `http://127.0.0.1:9222`.
3. Arc is logged in to `https://nmbot.nmnm.fun/` with access to the target group.

If several nmBot accounts are stored in the browser, set `sessionUsername` in `config.json`.
The tool fails on ambiguity instead of using the first account.

## Bootstrap the complete state

From this repository:

```bash
python3 nmbot_maintain.py pull
```

The command fetches both nmBot resources:

- `keywordReply` with `action: get`
- `messageTemplate` with `action: get`

It then writes the complete rule list to `nmbot-state.json` and externalizes each text template to
`replies/template-<id>.txt`. Existing unrelated files in `replies/` are not deleted automatically.

Review the resulting Git diff before treating it as the maintenance baseline.

## Daily workflow

### 1. Pull and review current remote state

```bash
python3 nmbot_maintain.py pull
git diff -- nmbot-state.json replies/
```

### 2. Edit locally

- Change matching behavior in the corresponding `keywordReplies` object.
- Change reply text in its `replies/template-<id>.txt` file.
- Change keyboard or link-preview behavior in the corresponding `messageTemplates` object.
- Preserve IDs and `replyMessageIds`; do not guess a missing relationship.

Reply text is final user-facing Telegram content. Keep repository paths, test notes, and maintainer
reasoning out of these files.

### 3. Validate locally

```bash
python3 nmbot_maintain.py validate
python3 -m unittest discover -s tests -v
```

Validation checks schema version, group identity, unique positive IDs, text files, and every
keyword-to-template reference.

### 4. Compare without writing nmBot

```bash
python3 nmbot_maintain.py diff
```

The report separates local-only, remote-only, changed, and unchanged IDs for both resource types.

### 5. Preview one targeted update

```bash
python3 nmbot_maintain.py apply --keyword-reply 123
python3 nmbot_maintain.py apply --message-template 43782
```

Without `--confirm`, the command only confirms that the selected local and remote objects exist and
differ. It exits with code `2` when an update would be made.

### 6. Apply one authorized update

```bash
python3 nmbot_maintain.py apply --keyword-reply 123 --confirm
python3 nmbot_maintain.py apply --message-template 43782 --confirm
```

Run only the target that was reviewed and authorized. A successful command ends with an nmBot
read-back of the same object.

### 7. Delete one authorized object

Remove the object from local state first and run `validate`. Preview the remote deletion without
writing:

```bash
python3 nmbot_maintain.py delete --keyword-reply 123
python3 nmbot_maintain.py delete --message-template 456
```

After reviewing the exact ID, repeat with `--confirm`. Delete a referencing keyword rule before its
message template. Each confirmed deletion is read back before the command reports success.

## Content sources

Repository-backed facts in replies should be verified against the owning source before editing,
including:

- `../SyncnextAPI/sourcesv3.json`
- `../SyncnextAPI/sources18.json`
- `../SyncnextAPI/source_ali.json`
- `../SyncnextPlugin_nqaq/sourcesv3_qoli.json`
- `../syncnextPlugin_icyl/sourcesv3-lite.json`
- `../syncnextPlugin_icyl/sourcesv3-18x.json`
- `../syncnextPlugin_icyl/sourcesv3.json`

Group-only short codes, moderation policy, and operator instructions still require current human
confirmation. Do not convert an unverified assumption into bot copy.

## Troubleshooting

### `auth_expired`

Open the nmBot panel in Arc, log in again, and rerun the read-only command. Do not copy a session ID
into a file or command line.

### Multiple browser sessions

Add the intended nmBot username as `sessionUsername` in `config.json`. The value may include or omit
the leading `@`.

### Local-only or remote-only IDs

Stop and reconcile the baseline. `apply` intentionally refuses to turn an edit into an implicit add
or delete.

### Read-back mismatch

Treat the update as unverified. Pull the live state again and inspect the exact returned object before
making another change.
