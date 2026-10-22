from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from context_compactor import ContextCompactor, extract_exact_references  # noqa: E402
from model_gateway import ModelGatewayError, ModelProfile  # noqa: E402
from workspace_store import WorkspaceChatStore  # noqa: E402


class FakeGateway:
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls = []

    def chat(self, messages, *, profile_id=None):
        self.calls.append((messages, profile_id))
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


class ContextCompactorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = WorkspaceChatStore(Path(self.temp_dir.name) / "chats.json")
        self.workspace_id = "workspace-a"
        self.profile = ModelProfile(
            profile_id="deepseek-test",
            display_name="DeepSeek Test",
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key_env="TEST_API_KEY",
            context_window_tokens=4096,
            compression_trigger_ratio=0.65,
            keep_recent_turns=2,
        )
        self.system_messages = [{"role": "system", "content": "workspace context"}]

    def tearDown(self):
        self.temp_dir.cleanup()

    def _append_turns(self, count: int, content_suffix: str = "") -> list[dict[str, str]]:
        for index in range(count):
            self.store.append(
                self.workspace_id,
                "user",
                f"问题 {index} {content_suffix}",
                self.profile.profile_id,
            )
            self.store.append(
                self.workspace_id,
                "assistant",
                f"回答 {index}",
                self.profile.profile_id,
            )
        return self.store.get_messages(self.workspace_id)

    @staticmethod
    def _summary_reply() -> str:
        return json.dumps(
            {
                "summary_version": 1,
                "user_goal": "完成 UE 场景构建",
                "confirmed_decisions": ["保留编码智能体"],
                "constraints": ["路径必须来自注册表"],
                "verified_facts": {"operation": "terrain.create"},
                "modified_files": [],
                "completed_work": ["地形链路已验证"],
                "open_tasks": ["实现道路"],
                "recent_failures": [],
            },
            ensure_ascii=False,
        )

    def test_force_compaction_keeps_raw_history_and_recent_turns(self):
        messages = self._append_turns(
            4,
            r'E:\UE_project\DEM\CS_01.tif /Game/M4.Material '
            '"operation": "terrain.create"',
        )
        gateway = FakeGateway(self._summary_reply())
        compactor = ContextCompactor(gateway, self.store)

        result = compactor.prepare(
            self.workspace_id,
            self.system_messages,
            messages,
            self.profile,
            force=True,
        )

        state = self.store.get_context_state(self.workspace_id)
        self.assertTrue(result.compressed_now)
        self.assertEqual(4, state["covered_message_count"])
        self.assertEqual(8, len(self.store.get_messages(self.workspace_id)))
        self.assertEqual(6, len(result.messages))  # system + snapshot + 最近两轮
        references = state["summary"]["exact_references"]
        self.assertIn("terrain.create", references["operation_names"])
        self.assertIn(r"E:\UE_project\DEM\CS_01.tif", references["windows_paths"])
        self.assertIn("/Game/M4.Material", references["unreal_asset_paths"])

    def test_below_threshold_does_not_call_compaction_model(self):
        messages = self._append_turns(2)
        gateway = FakeGateway(self._summary_reply())

        result = ContextCompactor(gateway, self.store).prepare(
            self.workspace_id,
            self.system_messages,
            messages,
            self.profile,
        )

        self.assertFalse(result.compressed_now)
        self.assertEqual([], gateway.calls)
        self.assertEqual(0, result.covered_message_count)

    def test_automatic_compaction_triggers_by_token_budget(self):
        messages = self._append_turns(4, "长内容" * 500)
        profile = ModelProfile(
            **{
                **self.profile.__dict__,
                "compression_trigger_ratio": 0.2,
            }
        )
        gateway = FakeGateway(self._summary_reply())

        result = ContextCompactor(gateway, self.store).prepare(
            self.workspace_id,
            self.system_messages,
            messages,
            profile,
        )

        self.assertTrue(result.compressed_now)
        self.assertEqual(1, len(gateway.calls))

    def test_failure_falls_back_without_modifying_raw_history(self):
        messages = self._append_turns(4, "内容" * 500)
        gateway = FakeGateway(error=ModelGatewayError("temporary failure"))

        result = ContextCompactor(gateway, self.store).prepare(
            self.workspace_id,
            self.system_messages,
            messages,
            self.profile,
            force=True,
        )

        self.assertEqual("fallback", result.status)
        self.assertIn("temporary failure", result.compression_error)
        self.assertEqual(8, len(self.store.get_messages(self.workspace_id)))
        self.assertEqual(0, self.store.get_context_state(self.workspace_id)["covered_message_count"])

    def test_exact_reference_extraction_does_not_rewrite_identifiers(self):
        references = extract_exact_references([
            {
                "role": "user",
                "content": (
                    r'读取 E:\UE_project\DEM\CS_01.tif，材质 /Game/M4.M4，'
                    '然后发送 {"operation": "terrain.create", "request_id": "r-1"}'
                ),
            }
        ])

        self.assertIn("terrain.create", references["operation_names"])
        self.assertIn("operation", references["json_keys"])
        self.assertIn("request_id", references["json_keys"])
        self.assertIn("/Game/M4.M4", references["unreal_asset_paths"])
        self.assertIn(r"E:\UE_project\DEM\CS_01.tif", references["windows_paths"])


if __name__ == "__main__":
    unittest.main()
