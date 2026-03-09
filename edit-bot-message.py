#!/usr/bin/env python3
"""Update nmBot message template via an existing logged-in Arc browser session."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MESSAGE_FILE = SCRIPT_DIR / "auto-message.txt"
DEFAULT_CONFIG_FILE = SCRIPT_DIR / "config.json"
DEFAULT_PLAYWRIGHT_CONFIG = SCRIPT_DIR / "playwright-arc-cdp-local-9222.json"
DEFAULT_SESSION_NAME = "nmbot-edit"
DEFAULT_PAGE_URL = "https://nmbot.nmnm.fun/#/groups/-1001717754522"
DEFAULT_API_BASE = "https://nmbot-api.nmnm.fun/messageTemplate"


class PlaywrightCliError(RuntimeError):
    pass


def run_playwright(args: list[str], check: bool = True) -> str:
    cmd = ["playwright-cli", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = proc.stdout.strip()
    error = proc.stderr.strip()

    if check and proc.returncode != 0:
        details = output or error or f"exit code {proc.returncode}"
        raise PlaywrightCliError(f"{' '.join(cmd)} failed: {details}")

    return output or error


def extract_result(output: str) -> str | None:
    marker = "### Result"
    if marker not in output:
        return None

    after = output.split(marker, 1)[1].lstrip()
    if "\n### " in after:
        result_block = after.split("\n### ", 1)[0]
    else:
        result_block = after
    return result_block.strip()


def eval_js(session_name: str, expression: str) -> str:
    output = run_playwright(["-s=" + session_name, "eval", expression])
    result = extract_result(output)
    if result is None:
        raise PlaywrightCliError(f"Unable to parse eval result from output:\n{output}")
    return result


def eval_json(session_name: str, expression: str) -> object:
    result = eval_js(session_name, expression)
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError as exc:
        raise PlaywrightCliError(f"Eval result is not valid JSON:\n{result}") from exc
    if isinstance(parsed, str):
        candidate = parsed.strip()
        if candidate.startswith("{") or candidate.startswith("["):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    return parsed


def ensure_browser_session(session_name: str, config_path: Path, page_url: str) -> None:
    # Best-effort close to avoid stale local daemon sessions with the same name.
    run_playwright(["-s=" + session_name, "close"], check=False)
    run_playwright(
        [
            "-s=" + session_name,
            "open",
            "--config",
            str(config_path),
            "--persistent",
        ]
    )
    run_playwright(["-s=" + session_name, "goto", page_url])


def load_message_text(message_file: Path) -> str:
    text = message_file.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Message file is empty: {message_file}")
    return text


def load_runtime_config(config_file: Path) -> dict[str, object]:
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {config_file}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Config file is not valid JSON: {config_file}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {config_file}")

    required = ("chatId", "templateId", "title", "keyboard")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"Config file is missing required keys: {', '.join(missing)}")

    if not isinstance(data["chatId"], str):
        raise ValueError("config.chatId must be a string")
    if not isinstance(data["templateId"], int):
        raise ValueError("config.templateId must be an integer")
    if not isinstance(data["title"], str):
        raise ValueError("config.title must be a string")
    if not isinstance(data["keyboard"], list):
        raise ValueError("config.keyboard must be a list")

    return data


def redact_secret(value: str, prefix: int = 8, suffix: int = 4) -> str:
    if len(value) <= prefix + suffix:
        return value
    return value[:prefix] + "..." + value[-suffix:]


def get_browser_auth_context(session_name: str) -> dict[str, object]:
    expression = (
        "JSON.stringify({"
        "href: location.href,"
        "sessions: JSON.parse(localStorage.getItem('sessions') || '[]'),"
        "telegramInitParams: sessionStorage.getItem('__telegram__initParams')"
        "})"
    )
    data = eval_json(session_name, expression)
    if not isinstance(data, dict):
        raise PlaywrightCliError("Browser auth context did not return an object.")
    return data


def build_payload(
    message_text: str,
    chat_id: str,
    template_id: int,
    title: str,
    keyboard: list[object],
) -> dict[str, object]:
    return {
        "action": "edit",
        "chatId": chat_id,
        "templateId": template_id,
        "data": {
            "title": title,
            "keyboard": keyboard,
            "protectContent": False,
            "disableNotification": False,
            "text": message_text,
            "parseMode": None,
            "linkPreview": False,
            "linkPreviewUrl": None,
            "linkPreviewPreferSmallMedia": False,
            "linkPreviewPreferLargeMedia": False,
            "linkPreviewShowAboveText": False,
        },
    }


def submit_update(api_base: str, payload: dict[str, object], session_id: str) -> dict[str, object]:
    url = api_base + "?session_id=" + urllib.parse.quote(session_id, safe="")
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=request_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://nmbot.nmnm.fun",
            "Referer": "https://nmbot.nmnm.fun/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            ),
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            raw_body = response.read().decode("utf-8", errors="replace")
            ok = 200 <= status < 300
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw_body = exc.read().decode("utf-8", errors="replace")
        ok = False
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach nmBot API: {exc}") from exc

    body: object = raw_body
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        pass

    return {
        "ok": ok,
        "status": status,
        "sessionId": session_id,
        "body": body,
    }


def summarize_response(response: dict[str, object], template_id: int) -> dict[str, object]:
    body = response.get("body")
    template_count = None
    updated_template = None

    if isinstance(body, dict):
        templates = body.get("messageTemplates")
        if isinstance(templates, list):
            template_count = len(templates)
            for item in templates:
                if isinstance(item, dict) and item.get("id") == template_id:
                    updated_template = {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "chatId": item.get("chatId"),
                        "textLength": len(item.get("text", "")) if isinstance(item.get("text"), str) else None,
                        "keyboardRows": len(item.get("keyboard", [])) if isinstance(item.get("keyboard"), list) else None,
                    }
                    break

    return {
        "ok": response.get("ok"),
        "status": response.get("status"),
        "sessionId": redact_secret(str(response.get("sessionId", ""))),
        "apiCode": body.get("code") if isinstance(body, dict) else None,
        "templateCount": template_count,
        "updatedTemplate": updated_template,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use a logged-in Arc browser session via playwright-cli to update an nmBot "
            "message template from auto-message.txt."
        )
    )
    parser.add_argument(
        "--message-file",
        type=Path,
        default=DEFAULT_MESSAGE_FILE,
        help=f"Plain text file used as message text (default: {DEFAULT_MESSAGE_FILE})",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help=f"JSON config file for chat/template/keyboard settings (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--playwright-config",
        type=Path,
        default=DEFAULT_PLAYWRIGHT_CONFIG,
        help="Playwright CDP config that connects to Arc.",
    )
    parser.add_argument(
        "--session-name",
        default=DEFAULT_SESSION_NAME,
        help=f"Named playwright-cli session (default: {DEFAULT_SESSION_NAME})",
    )
    parser.add_argument(
        "--page-url",
        default=DEFAULT_PAGE_URL,
        help=f"Management page to open before reading browser state (default: {DEFAULT_PAGE_URL})",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help=f"API base URL for template updates (default: {DEFAULT_API_BASE})",
    )
    parser.add_argument(
        "--chat-id",
        default=None,
        help="Override target Telegram chat id from config.json",
    )
    parser.add_argument(
        "--template-id",
        type=int,
        default=None,
        help="Override target template id from config.json",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Override template title from config.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not submit the update; only verify browser access and show payload summary.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep the playwright-cli session open after the script exits.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.message_file.exists():
        print(f"Message file not found: {args.message_file}", file=sys.stderr)
        return 1

    if not args.config_file.exists():
        print(f"Config file not found: {args.config_file}", file=sys.stderr)
        return 1

    if not args.playwright_config.exists():
        print(f"Playwright config not found: {args.playwright_config}", file=sys.stderr)
        return 1

    try:
        runtime_config = load_runtime_config(args.config_file)
        message_text = load_message_text(args.message_file)
        ensure_browser_session(args.session_name, args.playwright_config, args.page_url)
        auth_context = get_browser_auth_context(args.session_name)
        sessions = auth_context.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise PlaywrightCliError(
                "No logged-in nmBot sessions found in localStorage.sessions. "
                "Open nmBot in Arc and log in first."
            )
        if not isinstance(sessions[0], dict) or not sessions[0].get("sessionId"):
            raise PlaywrightCliError("The first nmBot browser session does not contain a usable sessionId.")
        session_id = str(sessions[0]["sessionId"])
        chat_id = str(args.chat_id if args.chat_id is not None else runtime_config["chatId"])
        template_id = int(args.template_id if args.template_id is not None else runtime_config["templateId"])
        title = str(args.title if args.title is not None else runtime_config["title"])
        keyboard = runtime_config["keyboard"]

        payload = build_payload(
            message_text=message_text,
            chat_id=chat_id,
            template_id=template_id,
            title=title,
            keyboard=keyboard,
        )

        summary = {
            "pageUrl": auth_context.get("href"),
            "messageFile": str(args.message_file),
            "configFile": str(args.config_file),
            "messageChars": len(message_text),
            "sessionCount": len(sessions),
            "sessionUser": (sessions[0] or {}).get("username"),
            "sessionId": redact_secret(session_id),
            "templateId": template_id,
            "chatId": chat_id,
            "title": title,
            "keyboardRows": len(keyboard) if isinstance(keyboard, list) else None,
            "dryRun": args.dry_run,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if args.dry_run:
            return 0

        response = submit_update(args.api_base, payload, session_id)
        print(json.dumps(summarize_response(response, template_id), ensure_ascii=False, indent=2))
        if not response.get("ok", False):
            return 1
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if not args.keep_open:
            run_playwright(["-s=" + args.session_name, "close"], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
