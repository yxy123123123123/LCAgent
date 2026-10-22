from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from action_protocol import ActionProtocol, ActionProtocolError  # noqa: E402


class ActionProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = ActionProtocol()

    def test_parses_plain_object(self):
        action = self.protocol.parse(
            json.dumps(
                {
                    "action": "finish",
                    "arguments": {"answer": "完成"},
                    "rationale": "已经取得工具结果",
                }
            )
        )

        self.assertEqual("finish", action.action)
        self.assertEqual({"answer": "完成"}, action.arguments)
        self.assertEqual("已经取得工具结果", action.rationale)

    def test_allows_single_json_code_fence(self):
        action = self.protocol.parse(
            '```json\n{"action":"finish","arguments":{"answer":"完成"}}\n```'
        )
        self.assertEqual("finish", action.action)

    def test_rejects_explanatory_text_around_json(self):
        with self.assertRaisesRegex(ActionProtocolError, "唯一且完整"):
            self.protocol.parse(
                '下面是动作：{"action":"finish","arguments":{"answer":"完成"}}'
            )

    def test_rejects_multiple_json_objects(self):
        with self.assertRaisesRegex(ActionProtocolError, "唯一且完整"):
            self.protocol.parse(
                '{"action":"finish","arguments":{}} {"action":"finish","arguments":{}}'
            )

    def test_rejects_unknown_top_level_field(self):
        with self.assertRaises(ActionProtocolError) as raised:
            self.protocol.parse(
                json.dumps(
                    {
                        "action": "finish",
                        "arguments": {},
                        "unexpected": True,
                    }
                )
            )
        self.assertEqual("INVALID_MODEL_ACTION", raised.exception.code)

    def test_rejects_missing_arguments(self):
        with self.assertRaises(ActionProtocolError):
            self.protocol.parse(json.dumps({"action": "finish"}))


if __name__ == "__main__":
    unittest.main()
