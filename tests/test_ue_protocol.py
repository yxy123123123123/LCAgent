import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
LCA_DIR = REPO_ROOT / "LCA"
if str(LCA_DIR) not in sys.path:
    sys.path.insert(0, str(LCA_DIR))

from ue_protocol import (  # noqa: E402
    UEContractMismatchError,
    UEProtocolClient,
    UEProtocolError,
    build_ue_request,
)


class UEProtocolTests(unittest.TestCase):
    def test_build_request_uses_uebox_style_envelope(self):
        request = build_ue_request(
            "get_scene_info",
            {"include_actors": False},
            request_id="task-001",
            project_path=r"E:\UE_project\LandscapeTool",
        )
        self.assertEqual("1.0", request["ver"])
        self.assertEqual("req", request["type"])
        self.assertEqual("get_scene_info", request["method"])
        self.assertEqual("task-001", request["id"])
        self.assertEqual(
            r"E:\UE_project\LandscapeTool",
            request["meta"]["lcagent"]["project_path"],
        )

    @patch("ue_protocol.send_mcp_command")
    def test_handshake_reads_capabilities(self, send):
        send.return_value = {
            "ver": "1.0",
            "type": "res",
            "id": "ignored-by-test",
            "ok": True,
            "data": {
                "contract_version": 1,
                "protocol_version": "1.0",
                "server": "UnrealMCP",
                "project_name": "LandscapeTool",
                "capabilities": [
                    "get_capabilities",
                    "query_scene_state",
                    "Landscaping",
                ],
                "scene_query": {
                    "supports_pagination": True,
                    "max_page_size": 1000,
                    "supported_fields": ["identity", "transform"],
                    "supported_filters": ["actor_name"],
                },
            },
        }
        # 请求 id 是随机生成的，响应不带 id 时仍允许单次 TCP 兼容服务。
        send.return_value.pop("id")
        info = UEProtocolClient().handshake()
        self.assertTrue(info.contract_supported)
        self.assertEqual("LandscapeTool", info.project_name)
        self.assertIn("Landscaping", info.capabilities)
        self.assertTrue(info.scene_query_supports_pagination)
        self.assertEqual(1000, info.scene_query_max_page_size)
        self.assertEqual(("identity", "transform"), info.scene_query_supported_fields)

    @patch("ue_protocol.send_mcp_command")
    def test_old_plugin_falls_back_to_read_only_scene_probe(self, send):
        send.side_effect = [
            {"status": "error", "message": "Unknown command: req"},
            {
                "status": "success",
                "result": {"level": "DemoMap", "actor_count": 3},
            },
        ]
        info = UEProtocolClient().handshake()
        self.assertTrue(info.legacy_fallback)
        self.assertFalse(info.contract_supported)
        self.assertEqual("DemoMap", info.project_name)
        self.assertEqual(2, send.call_count)

    @patch("ue_protocol.send_mcp_command")
    def test_contract_mismatch_is_not_silently_accepted(self, send):
        send.return_value = {
            "type": "res",
            "ok": True,
            "data": {"contract_version": 99, "capabilities": []},
        }
        with self.assertRaises(UEContractMismatchError):
            UEProtocolClient().handshake()

    @patch("ue_protocol.send_mcp_command")
    def test_response_id_mismatch_is_rejected(self, send):
        send.return_value = {
            "type": "res",
            "id": "another-request",
            "ok": True,
            "data": {},
        }
        with self.assertRaises(UEProtocolError):
            UEProtocolClient().request("get_scene_info")


if __name__ == "__main__":
    unittest.main()
