from __future__ import annotations

import os
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(
    os.environ.get(
        "LANDSCAPE_TOOL_PLUGINS",
        r"E:\UE_project\LandscapeTool\Plugins",
    )
)


@unittest.skipUnless(PLUGIN_ROOT.is_dir(), "LandscapeTool plugin checkout is unavailable")
class TerrainPluginContractTests(unittest.TestCase):
    def test_schema_snapshot_matches_plugin_source(self):
        agent_schema = REPO_ROOT / "contracts" / "unreal_mcp" / "terrain_create.schema.json"
        plugin_schema = (
            PLUGIN_ROOT
            / "UnrealMCP-master"
            / "Contracts"
            / "terrain_create.schema.json"
        )
        self.assertEqual(
            agent_schema.read_bytes(),
            plugin_schema.read_bytes(),
            "LCAgent 和 UnrealMCP 的地形协议快照发生漂移",
        )

    def test_json_fields_have_matching_cpp_handler_entries(self):
        handler = (
            PLUGIN_ROOT
            / "UnrealMCP-master"
            / "Source"
            / "UnrealMCP"
            / "Private"
            / "MCPCommandHandlers_Landscaping.cpp"
        ).read_text(encoding="utf-8")
        for required_source in (
            'CommandType.Equals(TEXT("Create")',
            'TryGetArrayField(TEXT("file_paths")',
            'TryGetStringField(TEXT("material_path")',
            'ImportTifFilesByPath(FilePaths)',
            'RasterImporterUI->CreateTerrain()',
        ):
            self.assertIn(required_source, handler)

    def test_landscaping_handler_is_registered(self):
        server_source = (
            PLUGIN_ROOT
            / "UnrealMCP-master"
            / "Source"
            / "UnrealMCP"
            / "Private"
            / "MCPTCPServer.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "RegisterCommandHandler(MakeShared<FMCPLandscapingCommandHandler>())",
            server_source,
        )


if __name__ == "__main__":
    unittest.main()
