import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import nmbot_maintain as maintain


class StateValidationTests(unittest.TestCase):
    def make_state(self, text_file: str = "reply.txt") -> dict:
        return {
            "schemaVersion": 1,
            "chatId": "-1001",
            "snapshot": {"complete": True},
            "keywordReplies": [
                {"id": 10, "chatId": "-1001", "replyMessageIds": [20]}
            ],
            "messageTemplates": [
                {"id": 20, "chatId": "-1001", "textFile": text_file}
            ],
        }

    def test_valid_state_hydrates_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reply.txt").write_text("hello\n", encoding="utf-8")
            state_file = root / "state.json"
            state = self.make_state()

            self.assertEqual(maintain.validate_state(state, state_file, "-1001"), [])
            hydrated = maintain.local_api_state(state, state_file)
            self.assertEqual(hydrated["messageTemplates"][0]["text"], "hello")
            self.assertNotIn("textFile", hydrated["messageTemplates"][0])

    def test_missing_template_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reply.txt").write_text("hello\n", encoding="utf-8")
            state = self.make_state()
            state["keywordReplies"][0]["replyMessageIds"] = [999]

            errors = maintain.validate_state(state, root / "state.json", "-1001")

            self.assertTrue(any("missing templates: [999]" in error for error in errors))

    def test_duplicate_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reply.txt").write_text("hello\n", encoding="utf-8")
            state = self.make_state()
            state["messageTemplates"].append(dict(state["messageTemplates"][0]))

            errors = maintain.validate_state(state, root / "state.json", "-1001")

            self.assertIn("duplicate messageTemplates id: 20", errors)


class ComparisonTests(unittest.TestCase):
    def test_comparison_classifies_ids(self) -> None:
        local = {
            "keywordReplies": [{"id": 1, "value": "local"}, {"id": 2}],
            "messageTemplates": [{"id": 10}],
        }
        remote = {
            "keywordReplies": [{"id": 1, "value": "remote"}, {"id": 3}],
            "messageTemplates": [{"id": 10}],
        }

        result = maintain.compare_states(local, remote)

        self.assertEqual(result["keywordReplies"]["localOnly"], [2])
        self.assertEqual(result["keywordReplies"]["remoteOnly"], [3])
        self.assertEqual(result["keywordReplies"]["changed"], [1])
        self.assertEqual(result["messageTemplates"]["unchanged"], [10])


class BrowserSessionSelectionTests(unittest.TestCase):
    def test_requires_explicit_username_when_ambiguous(self) -> None:
        sessions = [
            {"username": "one", "sessionId": "a"},
            {"username": "two", "sessionId": "b"},
        ]
        with self.assertRaises(maintain.MaintenanceError):
            maintain.select_browser_session(sessions, None)

        selected = maintain.select_browser_session(sessions, "@two")
        self.assertEqual(selected["sessionId"], "b")


class DeleteCommandTests(unittest.TestCase):
    def write_state(self, root: Path, keyword_replies: list, templates: list) -> Path:
        state_file = root / "state.json"
        state_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "chatId": "-1001",
                    "snapshot": {"complete": True},
                    "keywordReplies": keyword_replies,
                    "messageTemplates": templates,
                }
            ),
            encoding="utf-8",
        )
        return state_file

    def test_confirmed_keyword_delete_requires_local_absence_and_verifies_remote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = self.write_state(Path(directory), [], [])
            before = {
                "keywordReplies": [{"id": 10, "replyMessageIds": []}],
                "messageTemplates": [],
            }
            after = {"keywordReplies": [], "messageTemplates": []}
            args = SimpleNamespace(keyword_reply=10, message_template=None, confirm=True)
            config = {"chatId": "-1001", "apiBase": "https://example.invalid"}

            with patch.object(
                maintain, "fetch_remote_state", side_effect=[before, after]
            ), patch.object(maintain, "call_api") as call_api:
                result = maintain.command_delete(args, config, state_file, "session")

            self.assertEqual(result, 0)
            call_api.assert_called_once_with(
                "https://example.invalid",
                "keywordReply",
                {"action": "delete", "chatId": "-1001", "id": 10},
                "session",
            )

    def test_template_delete_rejects_remote_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = self.write_state(Path(directory), [], [])
            remote = {
                "keywordReplies": [{"id": 10, "replyMessageIds": [20]}],
                "messageTemplates": [{"id": 20}],
            }
            args = SimpleNamespace(keyword_reply=None, message_template=20, confirm=True)
            config = {"chatId": "-1001", "apiBase": "https://example.invalid"}

            with patch.object(maintain, "fetch_remote_state", return_value=remote):
                with self.assertRaisesRegex(
                    maintain.MaintenanceError, "remote references=\\[10\\]"
                ):
                    maintain.command_delete(args, config, state_file, "session")


if __name__ == "__main__":
    unittest.main()
