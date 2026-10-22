import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
LCA_DIR = REPO_ROOT / "LCA"
if str(LCA_DIR) not in sys.path:
    sys.path.insert(0, str(LCA_DIR))

from Agent import TraceStep  # noqa: E402
from run_manager import RunManager  # noqa: E402
from run_store import RunStore  # noqa: E402
from service import LCAgentService, ServiceError  # noqa: E402
from ue_protocol import UECapabilities  # noqa: E402
from workspace_store import WorkspaceChatStore, WorkspaceStore  # noqa: E402


class ServiceTests(unittest.TestCase):
    def test_health_and_route(self):
        service = LCAgentService()
        self.assertEqual("LCAgent", service.health()["service"])
        response = service.dispatch("GET", {"_path": "/api/health"})
        self.assertTrue(response["ok"])

    def test_unknown_route_is_structured_error(self):
        with self.assertRaises(ServiceError):
            LCAgentService().dispatch("GET", {"_path": "/api/not-found"})

    @patch("service.UEProtocolClient")
    def test_ue_handshake_returns_contract_fields(self, client_type):
        client_type.return_value.handshake.return_value = UECapabilities(
            contract_version=1,
            protocol_version="1.0",
            server="UnrealMCP",
            project_name="LandscapeTool",
            capabilities=("get_capabilities", "Landscaping"),
        )
        result = LCAgentService().ue_handshake({"project_path": "E:/LandscapeTool"})
        self.assertEqual(1, result["contract_version"])
        self.assertEqual("LandscapeTool", result["project_name"])
        self.assertTrue(result["contract_supported"])
        client_type.assert_called_once()


class ServiceFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "LandscapeTool"
        self.project.mkdir()
        self.service = LCAgentService()
        self.service.workspace_store = WorkspaceStore(self.root / "workspaces.json")
        self.service.chat_store = WorkspaceChatStore(self.root / "chats.json")
        self.workspace = self.service.workspace_store.add(str(self.project))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_remove_workspace_only_removes_entry(self):
        result = self.service.remove_workspace({"workspace_id": self.workspace.workspace_id})

        self.assertEqual(self.workspace.workspace_id, result["removed_workspace_id"])
        self.assertEqual([], self.service.workspace_store.list())
        self.assertTrue(self.project.is_dir())

    def test_clear_messages_removes_history_and_context_snapshot(self):
        self.service.chat_store.append(
            self.workspace.workspace_id, "user", "hello", "model-test"
        )
        self.service.chat_store.save_context_state(
            self.workspace.workspace_id,
            {
                "summary_version": 1,
                "covered_message_count": 1,
                "summary": {"user_goal": "test"},
            },
        )

        self.service.clear_messages({"workspace_id": self.workspace.workspace_id})

        self.assertEqual([], self.service.chat_store.get_messages(self.workspace.workspace_id))
        self.assertEqual(
            0,
            self.service.chat_store.get_context_state(self.workspace.workspace_id)[
                "covered_message_count"
            ],
        )

    @patch("service.ContextCompactor")
    @patch("service.ModelGateway")
    def test_manual_context_compaction_is_forced(self, gateway_type, compactor_type):
        profile = SimpleNamespace(profile_id="model-test")
        self.service.profile_store = MagicMock()
        self.service.profile_store.get.return_value = profile
        compactor_type.return_value.prepare.return_value = SimpleNamespace(
            status="compressed",
            compressed_now=True,
            covered_message_count=4,
            estimated_tokens=120,
            trigger_tokens=1000,
            context_window_tokens=2000,
            compression_error=None,
        )

        result = self.service.compact_context(
            {
                "workspace_id": self.workspace.workspace_id,
                "profile_id": "model-test",
            }
        )

        self.assertTrue(result["compressed_now"])
        self.assertTrue(compactor_type.return_value.prepare.call_args.kwargs["force"])
        gateway_type.assert_called_once_with(self.service.profile_store)

    @patch("service.ResourceRegistry")
    @patch("service.scan_project_assets")
    def test_ue_asset_scan_is_saved_and_summarised(self, scan, registry_type):
        scan.return_value = {
            "scan_revision": "scan-test",
            "assets": [
                {
                    "object_path": "/Game/Env/SM_Rock.SM_Rock",
                    "package_path": "/Game/Env",
                    "asset_name": "SM_Rock",
                    "asset_class": "StaticMesh",
                }
            ],
        }
        registry_type.return_value.ingest_scan.return_value = {
            "scan_revision": "scan-test",
            "received": 1,
        }

        result = self.service.scan_ue_assets(
            {"workspace_id": self.workspace.workspace_id, "package_roots": ["/Game"]}
        )

        self.assertEqual(1, result["asset_count"])
        self.assertEqual(2, result["folder_count"])
        registry_type.return_value.ingest_scan.assert_called_once()

    def test_uploaded_document_is_base64_decoded_before_ingestion(self):
        manager = MagicMock()
        manager.ingest_bytes.return_value = {"document_id": "doc-test"}
        self.service._knowledge_manager = MagicMock(return_value=manager)
        content = "道路规范".encode("utf-8")

        result = self.service.upload_knowledge_document(
            {
                "workspace_id": self.workspace.workspace_id,
                "kb_id": "kb-test",
                "filename": "规范.txt",
                "embedding_profile_id": "embedding-test",
                "data_base64": base64.b64encode(content).decode("ascii"),
            }
        )

        self.assertEqual("doc-test", result["document"]["document_id"])
        self.assertEqual(content, manager.ingest_bytes.call_args.args[2])


class _ServiceRunResult:
    final_answer = "运行完成"
    model_steps = 1
    dry_run = True

    def as_dict(self):
        return {
            "final_answer": self.final_answer,
            "trace": [],
            "model_steps": self.model_steps,
            "dry_run": self.dry_run,
        }


class _ServiceRunAgent:
    def __init__(self, **kwargs):
        self.trace_callback = kwargs["trace_callback"]

    def run_with_trace(self, _task_text):
        self.trace_callback(TraceStep(1, "ANALYZE", None, "running", {}))
        self.trace_callback(TraceStep(2, "FINISH", "finish", "success", {}))
        return _ServiceRunResult()


class ServiceRunTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "LandscapeTool"
        self.project.mkdir()
        self.run_store = RunStore(self.root / "lca.db", artifacts_root=self.root / "runs")
        self.run_manager = RunManager(self.run_store, agent_factory=_ServiceRunAgent)
        self.service = LCAgentService(
            run_store=self.run_store,
            run_manager=self.run_manager,
        )
        self.service.workspace_store = WorkspaceStore(self.root / "workspaces.json")
        self.workspace = self.service.workspace_store.add(str(self.project))

    def tearDown(self):
        self.run_manager.shutdown()
        self.temp_dir.cleanup()

    def test_async_run_routes_expose_status_events_and_artifacts(self):
        created = self.service.dispatch(
            "POST",
            {
                "_path": "/api/runs",
                "workspace_id": self.workspace.workspace_id,
                "task": "离线测试",
                "dry_run": True,
            },
        )
        run_id = created["run"]["run_id"]
        self.run_manager.wait(run_id, timeout=5)

        run = self.service.dispatch("GET", {"_path": f"/api/runs/{run_id}"})["run"]
        events = self.service.dispatch(
            "GET",
            {
                "_path": f"/api/runs/{run_id}/events",
                "_query": {"after_sequence": ["1"]},
            },
        )["events"]
        artifacts = self.service.dispatch(
            "GET", {"_path": f"/api/runs/{run_id}/artifacts"}
        )["artifacts"]

        self.assertEqual("succeeded", run["status"])
        self.assertTrue(all(item["sequence"] > 1 for item in events))
        self.assertEqual("agent_result.json", artifacts[0]["artifact_id"])

    def test_legacy_task_route_waits_for_new_run(self):
        response = self.service.dispatch(
            "POST",
            {
                "_path": "/api/tasks",
                "workspace_id": self.workspace.workspace_id,
                "task": "兼容测试",
            },
        )

        self.assertTrue(response["run_id"].startswith("run_"))
        self.assertEqual("运行完成", response["result"]["final_answer"])


class ServiceGoalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "LandscapeTool"
        self.project.mkdir()
        self.run_store = RunStore(self.root / "lca.db", artifacts_root=self.root / "runs")
        self.run_manager = RunManager(self.run_store, agent_factory=_ServiceRunAgent)
        self.service = LCAgentService(
            run_store=self.run_store,
            run_manager=self.run_manager,
            goal_data_root=self.root / "workspace_data",
        )
        self.service.workspace_store = WorkspaceStore(self.root / "workspaces.json")
        self.workspace = self.service.workspace_store.add(str(self.project))
        self.goal_document = json.loads(
            (REPO_ROOT / "contracts" / "examples" / "actor_x_goal_draft.json").read_text(
                encoding="utf-8"
            )
        )

    def tearDown(self):
        self.run_manager.shutdown()
        self.temp_dir.cleanup()

    def _save_draft(self):
        return self.service.dispatch(
            "POST",
            {
                "_path": "/api/goals/save",
                "workspace_id": self.workspace.workspace_id,
                "task_text": "把测试 Actor 的 X 坐标设置为 100 厘米",
                "goal": self.goal_document,
            },
        )

    def test_goal_can_be_saved_approved_and_compiled_to_plan(self):
        saved = self._save_draft()
        goal_id = saved["goal"]["goal_id"]
        approved = self.service.dispatch(
            "POST",
            {
                "_path": f"/api/goals/{goal_id}/approve",
                "workspace_id": self.workspace.workspace_id,
                "expected_draft_hash": saved["draft_hash"],
                "approved_by": "tester",
            },
        )
        planned = self.service.dispatch(
            "POST",
            {
                "_path": f"/api/goals/{goal_id}/observation-plan",
                "workspace_id": self.workspace.workspace_id,
                "supported_fields": ["identity", "transform"],
                "supports_pagination": True,
            },
        )
        listed = self.service.dispatch(
            "GET",
            {
                "_path": f"/api/workspaces/{self.workspace.workspace_id}/goals",
            },
        )

        self.assertEqual("approved", approved["goal"]["approval"]["status"])
        self.assertEqual(
            ["identity", "transform"],
            planned["observation_plan"]["queries"][0]["fields"],
        )
        self.assertEqual(1, len(listed["goals"]))

    def test_unapproved_goal_cannot_create_execution_plan(self):
        saved = self._save_draft()
        goal_id = saved["goal"]["goal_id"]

        with self.assertRaises(ServiceError) as raised:
            self.service.dispatch(
                "POST",
                {
                    "_path": f"/api/goals/{goal_id}/observation-plan",
                    "workspace_id": self.workspace.workspace_id,
                },
            )
        self.assertEqual(409, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
