from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from workspace_store import (  # noqa: E402
    WorkspaceChatStore,
    WorkspaceError,
    WorkspaceStore,
    build_workspace_context,
)


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace_a = self.root / "ProjectA"
        self.workspace_b = self.root / "ProjectB"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()
        (self.workspace_a / "ProjectA.uproject").write_text("{}", encoding="utf-8")
        (self.workspace_a / ".env").write_text("SECRET=value", encoding="utf-8")
        (self.workspace_a / "Content").mkdir()
        self.store = WorkspaceStore(self.root / "workspaces.json")
        self.chat_store = WorkspaceChatStore(self.root / "chats.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multiple_workspaces_can_be_added_and_selected(self):
        first = self.store.add(str(self.workspace_a), "Alpha")
        second = self.store.add(str(self.workspace_b), "Beta")

        self.assertEqual(2, len(self.store.list()))
        selected = self.store.select(first.workspace_id)
        self.assertEqual("Alpha", selected.name)
        self.assertNotEqual(first.workspace_id, second.workspace_id)

    def test_missing_workspace_is_rejected(self):
        with self.assertRaises(WorkspaceError):
            self.store.add(str(self.root / "missing"))

    def test_chat_history_is_isolated_by_workspace(self):
        first = self.store.add(str(self.workspace_a))
        second = self.store.add(str(self.workspace_b))
        self.chat_store.append(first.workspace_id, "user", "hello A", "deepseek-test")
        self.chat_store.append(second.workspace_id, "user", "hello B", "deepseek-test")

        self.assertEqual("hello A", self.chat_store.get_messages(first.workspace_id)[0]["content"])
        self.assertEqual("hello B", self.chat_store.get_messages(second.workspace_id)[0]["content"])

    def test_context_snapshot_does_not_replace_raw_messages(self):
        workspace = self.store.add(str(self.workspace_a))
        self.chat_store.append(workspace.workspace_id, "user", "first", "deepseek-test")
        self.chat_store.append(workspace.workspace_id, "assistant", "second", "deepseek-test")

        self.chat_store.save_context_state(
            workspace.workspace_id,
            {
                "summary_version": 1,
                "covered_message_count": 1,
                "summary": {"user_goal": "test"},
            },
        )

        self.assertEqual(2, len(self.chat_store.get_messages(workspace.workspace_id)))
        state = self.chat_store.get_context_state(workspace.workspace_id)
        self.assertEqual(1, state["covered_message_count"])
        self.assertEqual("test", state["summary"]["user_goal"])

    def test_clearing_conversation_also_clears_context_snapshot(self):
        workspace = self.store.add(str(self.workspace_a))
        self.chat_store.append(workspace.workspace_id, "user", "first", "deepseek-test")
        self.chat_store.save_context_state(
            workspace.workspace_id,
            {
                "summary_version": 1,
                "covered_message_count": 1,
                "summary": {"user_goal": "test"},
            },
        )

        self.chat_store.clear(workspace.workspace_id)

        self.assertEqual([], self.chat_store.get_messages(workspace.workspace_id))
        self.assertEqual(0, self.chat_store.get_context_state(workspace.workspace_id)["covered_message_count"])

    def test_context_excludes_env_secret_file(self):
        context = build_workspace_context(str(self.workspace_a))
        self.assertIn("ProjectA.uproject", context)
        self.assertIn("Content", context)
        self.assertNotIn(".env", context)
        self.assertNotIn("SECRET", context)


if __name__ == "__main__":
    unittest.main()
