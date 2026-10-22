from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from Agent import AgentState, DecisionAgent  # noqa: E402
from model_gateway import ModelReply  # noqa: E402


class FakeModelGateway:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def chat(self, messages, *, profile_id=None):
        self.calls.append(list(messages))
        return ModelReply(
            content=next(self.replies),
            profile_id=profile_id or "test",
            provider="test",
            model="test",
            usage={},
            response_metadata={},
        )


class FakeRetrievalService:
    def __init__(self):
        self.knowledge_base = SimpleNamespace(list_bases=lambda: [])
        self.resource_registry = SimpleNamespace(stats=lambda: {"confirmed": 1})

    def search_assets(self, query, *, asset_class=None, limit=10):
        return {
            "tool": "search_assets",
            "results": [{"object_path": "/Game/M.M", "user_status": "confirmed"}],
        }

    def search_knowledge(self, query, *, embedding_profile_id, kb_ids=None, top_k=5):
        return {"tool": "search_knowledge", "results": []}


class FakeRuntime:
    scripts_dir = Path("C:/fake/LCA")

    def write_generated_script(self, filename, content):
        return {"status": "written", "path": filename}

    def execute_generated_script(self, filename, argv, *, dry_run):
        return {"status": "success", "mode": "dry_run" if dry_run else "live"}


class AgentStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_structured_actions_follow_explicit_states(self):
        replies = [
            json.dumps({"action": "search_assets", "arguments": {"query": "地形材质"}}),
            json.dumps(
                {
                    "action": "write_script",
                    "arguments": {
                        "filename": "generated_terrain.py",
                        "content": "from create_terrain import main\nraise SystemExit(main())\n",
                    },
                }
            ),
            json.dumps(
                {
                    "action": "execute_script",
                    "arguments": {"filename": "generated_terrain.py", "argv": []},
                }
            ),
            json.dumps({"action": "finish", "arguments": {"answer": "离线 JSON 已验证"}}),
        ]
        agent = DecisionAgent(
            self.temp_dir.name,
            "chat-test",
            workspace_id="workspace-test",
            embedding_profile_id="embedding-test",
            model_gateway=FakeModelGateway(replies),
            retrieval_service=FakeRetrievalService(),
            execution_runtime=FakeRuntime(),
            dry_run=True,
        )
        result = agent.run_with_trace("创建地形")
        states = [item.state for item in result.trace]

        self.assertEqual("离线 JSON 已验证", result.final_answer)
        for state in (
            AgentState.RETRIEVE.value,
            AgentState.GENERATE.value,
            AgentState.VALIDATE.value,
            AgentState.EXECUTE.value,
            AgentState.OBSERVE.value,
            AgentState.FINISH.value,
        ):
            self.assertIn(state, states)

    def test_invalid_model_output_enters_repair_then_recovers(self):
        agent = DecisionAgent(
            self.temp_dir.name,
            model_gateway=FakeModelGateway(
                [
                    "not json",
                    json.dumps({"action": "finish", "arguments": {"answer": "已修复"}}),
                ]
            ),
            retrieval_service=FakeRetrievalService(),
            execution_runtime=FakeRuntime(),
        )
        result = agent.run_with_trace("测试")
        self.assertIn(AgentState.REPAIR.value, [item.state for item in result.trace])
        self.assertEqual("已修复", result.final_answer)

    def test_unretrieved_unreal_path_is_rejected_before_write(self):
        agent = DecisionAgent(
            self.temp_dir.name,
            model_gateway=FakeModelGateway(
                [
                    json.dumps(
                        {
                            "action": "write_script",
                            "arguments": {
                                "filename": "generated_task.py",
                                "content": "MATERIAL = '/Game/Invented.M_Invented'\n",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "action": "finish",
                            "arguments": {"answer": "缺少已确认资源，未写入脚本"},
                        }
                    ),
                ]
            ),
            retrieval_service=FakeRetrievalService(),
            execution_runtime=FakeRuntime(),
        )
        result = agent.run_with_trace("测试未授权路径")
        repairs = [item for item in result.trace if item.state == AgentState.REPAIR.value]
        self.assertEqual(1, len(repairs))
        self.assertIn("未经本次", repairs[0].detail["message"])


if __name__ == "__main__":
    unittest.main()
