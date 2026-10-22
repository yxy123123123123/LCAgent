from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from goal_compiler import GoalCompiler, GoalCompilerError  # noqa: E402


class FakeGateway:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def chat(self, messages, *, profile_id=None):
        self.calls.append((messages, profile_id))
        return SimpleNamespace(
            content=self.content,
            profile_id=profile_id or "fake-profile",
            model="fake-model",
            usage={"input_tokens": 10},
        )


class GoalCompilerTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(
            (REPO_ROOT / "contracts" / "examples" / "actor_x_goal_draft.json").read_text(
                encoding="utf-8"
            )
        )

    def test_model_output_becomes_unapproved_draft(self):
        gateway = FakeGateway(json.dumps(self.document, ensure_ascii=False))
        result = GoalCompiler(gateway).compile("Actor X 设置为 100", profile_id="profile-a")

        self.assertFalse(result.goal.approved)
        self.assertEqual("profile-a", result.profile_id)
        self.assertEqual("fake-model", result.model)
        self.assertEqual(1, len(gateway.calls))

    def test_model_cannot_self_approve(self):
        self.document["approval"] = {
            "status": "approved",
            "draft_hash": "a" * 64,
            "approved_at": "2026-01-01T00:00:00Z",
            "approved_by": "model",
            "frozen_hash": "a" * 64,
        }
        goal = GoalCompiler.compile_document("测试", self.document)
        self.assertEqual("draft", goal.approval["status"])

    def test_explanatory_text_around_json_is_rejected(self):
        gateway = FakeGateway("下面是结果：" + json.dumps(self.document, ensure_ascii=False))
        with self.assertRaisesRegex(GoalCompilerError, "唯一且完整"):
            GoalCompiler(gateway).compile("测试")

    def test_empty_task_is_rejected_before_model_call(self):
        gateway = FakeGateway(json.dumps(self.document, ensure_ascii=False))

        with self.assertRaisesRegex(GoalCompilerError, "不能为空"):
            GoalCompiler(gateway).compile("   ")
        self.assertEqual([], gateway.calls)


if __name__ == "__main__":
    unittest.main()
