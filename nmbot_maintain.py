#!/usr/bin/env python3
"""Maintain all nmBot keyword replies and their referenced message templates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = SCRIPT_DIR / "config.json"
DEFAULT_PLAYWRIGHT_CONFIG = SCRIPT_DIR / "playwright-arc-cdp-local-9222.json"
DEFAULT_SESSION_NAME = "nmbot-maintain"


class MaintenanceError(RuntimeError):
    """Raised when local state or the nmBot runtime contract is invalid."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MaintenanceError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MaintenanceError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_config(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise MaintenanceError(f"Config must be a JSON object: {path}")

    required_types = {
        "chatId": str,
        "pageUrl": str,
        "apiBase": str,
        "stateFile": str,
    }
    for key, expected_type in required_types.items():
        if not isinstance(value.get(key), expected_type):
            raise MaintenanceError(f"config.{key} must be a {expected_type.__name__}")

    username = value.get("sessionUsername")
    if username is not None and not isinstance(username, str):
        raise MaintenanceError("config.sessionUsername must be a string or null")
    return value


def resolve_from_config(config_file: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return config_file.resolve().parent / path


def run_playwright(args: list[str], check: bool = True) -> str:
    command = ["playwright-cli", *args]
    process = subprocess.run(command, capture_output=True, text=True)
    output = process.stdout.strip() or process.stderr.strip()
    if check and process.returncode != 0:
        raise MaintenanceError(
            f"{' '.join(command)} failed: {output or f'exit code {process.returncode}'}"
        )
    return output


def extract_playwright_result(output: str) -> str:
    marker = "### Result"
    if marker not in output:
        raise MaintenanceError(f"Unable to parse playwright-cli output:\n{output}")
    result = output.split(marker, 1)[1].lstrip()
    if "\n### " in result:
        result = result.split("\n### ", 1)[0]
    return result.strip()


def open_arc_session(
    session_name: str,
    playwright_config: Path,
    page_url: str,
) -> None:
    run_playwright([f"-s={session_name}", "close"], check=False)
    run_playwright(
        [
            f"-s={session_name}",
            "open",
            "--config",
            str(playwright_config),
            "--persistent",
        ]
    )
    run_playwright([f"-s={session_name}", "goto", page_url])


def read_browser_sessions(session_name: str) -> list[dict[str, Any]]:
    expression = "JSON.stringify(JSON.parse(localStorage.getItem('sessions') || '[]'))"
    output = run_playwright([f"-s={session_name}", "eval", expression])
    raw = extract_playwright_result(output)
    try:
        value = json.loads(raw)
        if isinstance(value, str):
            value = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MaintenanceError("nmBot browser sessions are not valid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise MaintenanceError("nmBot browser sessions must be a list of objects")
    return value


def select_browser_session(
    sessions: list[dict[str, Any]],
    expected_username: str | None,
) -> dict[str, Any]:
    usable = [item for item in sessions if isinstance(item.get("sessionId"), str)]
    if expected_username:
        usable = [
            item
            for item in usable
            if str(item.get("username", "")).lstrip("@").casefold()
            == expected_username.lstrip("@").casefold()
        ]
    if not usable:
        suffix = f" for @{expected_username.lstrip('@')}" if expected_username else ""
        raise MaintenanceError(f"No usable logged-in nmBot session found{suffix}")
    if len(usable) != 1:
        raise MaintenanceError(
            "Multiple usable nmBot sessions found; set config.sessionUsername explicitly"
        )
    return usable[0]


def call_api(
    api_base: str,
    resource: str,
    payload: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    url = (
        api_base.rstrip("/")
        + "/"
        + resource
        + "?session_id="
        + urllib.parse.quote(session_id, safe="")
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://nmbot.nmnm.fun",
            "Referer": "https://nmbot.nmnm.fun/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise MaintenanceError(f"Unable to reach nmBot API: {exc}") from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MaintenanceError(f"nmBot API returned non-JSON HTTP {status}") from exc
    if not isinstance(body, dict):
        raise MaintenanceError(f"nmBot API returned an unexpected HTTP {status} payload")
    if status < 200 or status >= 300 or body.get("code") != 200:
        reason = body.get("message") or body.get("msg") or f"api_code_{body.get('code')}"
        raise MaintenanceError(f"nmBot API {resource} failed: HTTP {status}, {reason}")
    return body


def fetch_remote_state(
    config: dict[str, Any],
    session_id: str,
) -> dict[str, list[dict[str, Any]]]:
    chat_id = config["chatId"]
    keyword_body = call_api(
        config["apiBase"],
        "keywordReply",
        {"action": "get", "chatId": chat_id},
        session_id,
    )
    template_body = call_api(
        config["apiBase"],
        "messageTemplate",
        {"action": "get", "chatId": chat_id},
        session_id,
    )
    keyword_replies = keyword_body.get("keywordReplies")
    message_templates = template_body.get("messageTemplates")
    if not isinstance(keyword_replies, list) or not all(
        isinstance(item, dict) for item in keyword_replies
    ):
        raise MaintenanceError("keywordReply get response has no valid keywordReplies list")
    if not isinstance(message_templates, list) or not all(
        isinstance(item, dict) for item in message_templates
    ):
        raise MaintenanceError("messageTemplate get response has no valid messageTemplates list")
    return {
        "keywordReplies": keyword_replies,
        "messageTemplates": message_templates,
    }


def externalize_templates(
    templates: list[dict[str, Any]],
    state_file: Path,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    replies_dir = state_file.parent / "replies"
    for source in sorted(templates, key=lambda item: int(item.get("id", 0))):
        item = dict(source)
        text = item.pop("text", None)
        if isinstance(text, str):
            relative = Path("replies") / f"template-{item.get('id')}.txt"
            text_path = state_file.parent / relative
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text + "\n", encoding="utf-8")
            item["textFile"] = relative.as_posix()
        result.append(item)
    replies_dir.mkdir(parents=True, exist_ok=True)
    return result


def hydrate_templates(
    templates: list[dict[str, Any]],
    state_file: Path,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in templates:
        item = dict(source)
        text_file = item.pop("textFile", None)
        if text_file is not None:
            if not isinstance(text_file, str):
                raise MaintenanceError("messageTemplate.textFile must be a string")
            path = resolve_from_config(state_file, text_file)
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise MaintenanceError(f"Template text file not found: {path}") from exc
            item["text"] = text.removesuffix("\n")
        result.append(item)
    return result


def load_local_state(state_file: Path) -> dict[str, Any]:
    value = read_json(state_file)
    if not isinstance(value, dict):
        raise MaintenanceError(f"State must be a JSON object: {state_file}")
    return value


def validate_state(
    state: dict[str, Any],
    state_file: Path,
    expected_chat_id: str,
) -> list[str]:
    errors: list[str] = []
    if state.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if state.get("chatId") != expected_chat_id:
        errors.append("state.chatId does not match config.chatId")

    keyword_replies = state.get("keywordReplies")
    templates = state.get("messageTemplates")
    if not isinstance(keyword_replies, list):
        errors.append("keywordReplies must be a list")
        keyword_replies = []
    if not isinstance(templates, list):
        errors.append("messageTemplates must be a list")
        templates = []

    def collect_ids(items: list[Any], label: str) -> set[int]:
        ids: set[int] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{label}[{index}] must be an object")
                continue
            item_id = item.get("id")
            if not isinstance(item_id, int) or item_id <= 0:
                errors.append(f"{label}[{index}].id must be a positive integer")
            elif item_id in ids:
                errors.append(f"duplicate {label} id: {item_id}")
            else:
                ids.add(item_id)
        return ids

    keyword_ids = collect_ids(keyword_replies, "keywordReplies")
    template_ids = collect_ids(templates, "messageTemplates")
    del keyword_ids

    for item in keyword_replies:
        if not isinstance(item, dict):
            continue
        references = item.get("replyMessageIds")
        if not isinstance(references, list) or not all(
            isinstance(value, int) for value in references
        ):
            errors.append(
                f"keywordReply {item.get('id')} replyMessageIds must be a list of integers"
            )
            continue
        missing = sorted(set(references) - template_ids)
        if missing:
            errors.append(
                f"keywordReply {item.get('id')} references missing templates: {missing}"
            )

    try:
        hydrate_templates(
            [item for item in templates if isinstance(item, dict)],
            state_file,
        )
    except MaintenanceError as exc:
        errors.append(str(exc))
    return errors


def object_map(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(item["id"]): item
        for item in items
        if isinstance(item.get("id"), int) and int(item["id"]) > 0
    }


def compare_states(
    local: dict[str, list[dict[str, Any]]],
    remote: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, list[int]]]:
    result: dict[str, dict[str, list[int]]] = {}
    for key in ("keywordReplies", "messageTemplates"):
        local_map = object_map(local[key])
        remote_map = object_map(remote[key])
        result[key] = {
            "localOnly": sorted(local_map.keys() - remote_map.keys()),
            "remoteOnly": sorted(remote_map.keys() - local_map.keys()),
            "changed": sorted(
                item_id
                for item_id in local_map.keys() & remote_map.keys()
                if local_map[item_id] != remote_map[item_id]
            ),
            "unchanged": sorted(
                item_id
                for item_id in local_map.keys() & remote_map.keys()
                if local_map[item_id] == remote_map[item_id]
            ),
        }
    return result


def local_api_state(state: dict[str, Any], state_file: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "keywordReplies": [dict(item) for item in state["keywordReplies"]],
        "messageTemplates": hydrate_templates(state["messageTemplates"], state_file),
    }


def command_validate(config: dict[str, Any], state_file: Path) -> int:
    state = load_local_state(state_file)
    errors = validate_state(state, state_file, config["chatId"])
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), dict) else {}
    print(
        json.dumps(
            {
                "valid": True,
                "completeSnapshot": snapshot.get("complete") is True,
                "keywordReplies": len(state["keywordReplies"]),
                "messageTemplates": len(state["messageTemplates"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_pull(
    config: dict[str, Any],
    state_file: Path,
    session_id: str,
) -> int:
    remote = fetch_remote_state(config, session_id)
    state = {
        "schemaVersion": 1,
        "chatId": config["chatId"],
        "snapshot": {
            "complete": True,
            "pulledAt": datetime.now(timezone.utc).isoformat(),
        },
        "keywordReplies": sorted(
            remote["keywordReplies"], key=lambda item: int(item.get("id", 0))
        ),
        "messageTemplates": externalize_templates(
            remote["messageTemplates"], state_file
        ),
    }
    errors = validate_state(state, state_file, config["chatId"])
    if errors:
        raise MaintenanceError("Pulled state is invalid: " + "; ".join(errors))
    write_json(state_file, state)
    print(
        json.dumps(
            {
                "pulled": True,
                "stateFile": str(state_file),
                "keywordReplies": len(state["keywordReplies"]),
                "messageTemplates": len(state["messageTemplates"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_diff(
    config: dict[str, Any],
    state_file: Path,
    session_id: str,
) -> int:
    state = load_local_state(state_file)
    errors = validate_state(state, state_file, config["chatId"])
    if errors:
        raise MaintenanceError("Local state is invalid: " + "; ".join(errors))
    comparison = compare_states(
        local_api_state(state, state_file),
        fetch_remote_state(config, session_id),
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    has_difference = any(
        details[name]
        for details in comparison.values()
        for name in ("localOnly", "remoteOnly", "changed")
    )
    return 2 if has_difference else 0


def command_apply(
    args: argparse.Namespace,
    config: dict[str, Any],
    state_file: Path,
    session_id: str,
) -> int:
    state = load_local_state(state_file)
    errors = validate_state(state, state_file, config["chatId"])
    if errors:
        raise MaintenanceError("Local state is invalid: " + "; ".join(errors))
    local = local_api_state(state, state_file)
    remote = fetch_remote_state(config, session_id)

    if args.keyword_reply is not None:
        key = "keywordReplies"
        resource = "keywordReply"
        item_id = args.keyword_reply
    else:
        key = "messageTemplates"
        resource = "messageTemplate"
        item_id = args.message_template

    local_item = object_map(local[key]).get(item_id)
    remote_item = object_map(remote[key]).get(item_id)
    if local_item is None:
        raise MaintenanceError(f"Local {resource} id {item_id} does not exist")
    if remote_item is None:
        raise MaintenanceError(
            f"Remote {resource} id {item_id} does not exist; add/delete is not implicit"
        )
    if local_item == remote_item:
        print(f"No change for {resource} id {item_id}.")
        return 0

    if not args.confirm:
        print(
            json.dumps(
                {
                    "wouldApply": True,
                    "resource": resource,
                    "id": item_id,
                    "chatId": config["chatId"],
                    "hint": "Repeat with --confirm to update this one existing object.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if resource == "keywordReply":
        payload = {
            "action": "edit",
            "chatId": config["chatId"],
            "id": item_id,
            "newObject": local_item,
        }
    else:
        data = {
            key: value
            for key, value in local_item.items()
            if key not in {"id", "chatId"}
        }
        payload = {
            "action": "edit",
            "chatId": config["chatId"],
            "templateId": item_id,
            "data": data,
        }
    call_api(config["apiBase"], resource, payload, session_id)

    verified = fetch_remote_state(config, session_id)
    if object_map(verified[key]).get(item_id) != local_item:
        raise MaintenanceError(
            f"nmBot accepted the request but read-back differs for {resource} id {item_id}"
        )
    print(f"Updated and verified {resource} id {item_id}.")
    return 0


def command_delete(
    args: argparse.Namespace,
    config: dict[str, Any],
    state_file: Path,
    session_id: str,
) -> int:
    state = load_local_state(state_file)
    errors = validate_state(state, state_file, config["chatId"])
    if errors:
        raise MaintenanceError("Local state is invalid: " + "; ".join(errors))
    local = local_api_state(state, state_file)
    remote = fetch_remote_state(config, session_id)

    if args.keyword_reply is not None:
        key = "keywordReplies"
        resource = "keywordReply"
        item_id = args.keyword_reply
    else:
        key = "messageTemplates"
        resource = "messageTemplate"
        item_id = args.message_template

    if object_map(local[key]).get(item_id) is not None:
        raise MaintenanceError(
            f"Local {resource} id {item_id} still exists; remove it locally and validate first"
        )
    if object_map(remote[key]).get(item_id) is None:
        print(f"Remote {resource} id {item_id} is already absent.")
        return 0

    if resource == "messageTemplate":
        local_references = sorted(
            int(item["id"])
            for item in local["keywordReplies"]
            if item_id in item.get("replyMessageIds", [])
        )
        remote_references = sorted(
            int(item["id"])
            for item in remote["keywordReplies"]
            if item_id in item.get("replyMessageIds", [])
        )
        if local_references or remote_references:
            raise MaintenanceError(
                f"Cannot delete messageTemplate id {item_id}; "
                f"local references={local_references}, remote references={remote_references}"
            )

    if not args.confirm:
        print(
            json.dumps(
                {
                    "wouldDelete": True,
                    "resource": resource,
                    "id": item_id,
                    "chatId": config["chatId"],
                    "hint": "Repeat with --confirm to delete this one remote object.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if resource == "keywordReply":
        payload = {
            "action": "delete",
            "chatId": config["chatId"],
            "id": item_id,
        }
    else:
        payload = {
            "action": "delete",
            "chatId": config["chatId"],
            "templateId": item_id,
        }
    call_api(config["apiBase"], resource, payload, session_id)

    verified = fetch_remote_state(config, session_id)
    if object_map(verified[key]).get(item_id) is not None:
        raise MaintenanceError(
            f"nmBot accepted the request but {resource} id {item_id} still exists"
        )
    print(f"Deleted and verified {resource} id {item_id}.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Maintain all nmBot keyword replies and message templates."
    )
    parser.add_argument("--config-file", type=Path, default=DEFAULT_CONFIG_FILE)
    parser.add_argument(
        "--playwright-config", type=Path, default=DEFAULT_PLAYWRIGHT_CONFIG
    )
    parser.add_argument("--session-name", default=DEFAULT_SESSION_NAME)
    parser.add_argument("--keep-open", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate only the tracked local state.")
    subparsers.add_parser("pull", help="Pull every remote rule and template locally.")
    subparsers.add_parser("diff", help="Compare tracked state with nmBot read-only.")
    apply_parser = subparsers.add_parser(
        "apply", help="Update exactly one existing rule or template."
    )
    target = apply_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--keyword-reply", type=int, metavar="ID")
    target.add_argument("--message-template", type=int, metavar="ID")
    apply_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Perform the remote update after showing the default no-write plan.",
    )
    delete_parser = subparsers.add_parser(
        "delete", help="Delete exactly one object that is already absent locally."
    )
    delete_target = delete_parser.add_mutually_exclusive_group(required=True)
    delete_target.add_argument("--keyword-reply", type=int, metavar="ID")
    delete_target.add_argument("--message-template", type=int, metavar="ID")
    delete_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Perform the remote deletion after showing the default no-write plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config_file = args.config_file.resolve()
        config = load_config(config_file)
        state_file = resolve_from_config(config_file, config["stateFile"])
        if args.command == "validate":
            return command_validate(config, state_file)

        open_arc_session(args.session_name, args.playwright_config, config["pageUrl"])
        sessions = read_browser_sessions(args.session_name)
        browser_session = select_browser_session(
            sessions, config.get("sessionUsername")
        )
        session_id = str(browser_session["sessionId"])
        if args.command == "pull":
            return command_pull(config, state_file, session_id)
        if args.command == "diff":
            return command_diff(config, state_file, session_id)
        if args.command == "apply":
            return command_apply(args, config, state_file, session_id)
        if args.command == "delete":
            return command_delete(args, config, state_file, session_id)
        raise MaintenanceError(f"Unsupported command: {args.command}")
    except MaintenanceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if getattr(args, "command", None) != "validate" and not args.keep_open:
            run_playwright([f"-s={args.session_name}", "close"], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
