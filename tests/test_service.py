import base64
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


if __name__ == "__main__":
    unittest.main()
