from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from tool_registry import (  # noqa: E402
    ToolEffect,
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
)


INPUT_SCHEMA = {
    "type": "object",
    "required": ["value"],
    "properties": {"value": {"type": "integer"}},
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["doubled"],
    "properties": {"doubled": {"type": "integer"}},
    "additionalProperties": False,
}


class ToolRegistryTests(unittest.TestCase):
    def _registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="double",
                description="将整数乘以二",
                input_schema=INPUT_SCHEMA,
                output_schema=OUTPUT_SCHEMA,
                required_capabilities=("math.double.v1",),
                effect=ToolEffect.READ_ONLY,
                idempotent=True,
            ),
            lambda arguments: {"doubled": arguments["value"] * 2},
        )
        return registry

    def test_executes_registered_tool_after_validation(self):
        result = self._registry().execute(
            "double",
            {"value": 4},
            available_capabilities={"math.double.v1"},
            allowed_effects={ToolEffect.READ_ONLY},
        )
        self.assertEqual({"doubled": 8}, result)

    def test_rejects_unknown_argument(self):
        with self.assertRaises(ToolRegistryError) as raised:
            self._registry().execute(
                "double",
                {"value": 4, "unexpected": True},
                available_capabilities={"math.double.v1"},
            )
        self.assertEqual("INVALID_TOOL_ARGUMENTS", raised.exception.code)

    def test_rejects_missing_capability(self):
        with self.assertRaises(ToolRegistryError) as raised:
            self._registry().execute(
                "double",
                {"value": 4},
                available_capabilities=set(),
            )
        self.assertEqual("TOOL_CAPABILITY_MISSING", raised.exception.code)

    def test_rejects_disallowed_effect(self):
        with self.assertRaises(ToolRegistryError) as raised:
            self._registry().execute(
                "double",
                {"value": 4},
                available_capabilities={"math.double.v1"},
                allowed_effects={ToolEffect.LOCAL_WRITE},
            )
        self.assertEqual("TOOL_EFFECT_NOT_ALLOWED", raised.exception.code)

    def test_rejects_invalid_output(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="invalid_output",
                description="返回错误格式",
                input_schema={"type": "object", "additionalProperties": False},
                output_schema=OUTPUT_SCHEMA,
            ),
            lambda _: {"doubled": "not-an-integer"},
        )
        with self.assertRaises(ToolRegistryError) as raised:
            registry.execute("invalid_output", {})
        self.assertEqual("INVALID_TOOL_OUTPUT", raised.exception.code)

    def test_prompt_catalog_comes_from_registered_spec(self):
        catalog = self._registry().render_prompt_catalog()
        self.assertIn("double", catalog)
        self.assertIn("将整数乘以二", catalog)
        self.assertIn("read_only", catalog)


if __name__ == "__main__":
    unittest.main()
