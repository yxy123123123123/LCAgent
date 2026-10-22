from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from resource_registry import ResourceRegistry  # noqa: E402


class ResourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = ResourceRegistry("workspace-test", data_root=Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_only_confirmed_existing_assets_are_searchable(self):
        self.registry.ingest_scan(
            [
                {
                    "object_path": "/Game/Materials/M_Terrain.M_Terrain",
                    "asset_name": "M_Terrain",
                    "asset_class": "/Script/Engine.Material",
                    "description": "山地地形材质",
                    "purposes": ["地形材质"],
                }
            ],
            scan_revision="scan-1",
        )
        self.assertEqual([], self.registry.search("地形材质"))

        resource = self.registry.list_resources()[0]
        self.registry.update_confirmation(resource["id"], user_status="confirmed")
        matches = self.registry.search("地形材质")
        self.assertEqual("/Game/Materials/M_Terrain.M_Terrain", matches[0]["object_path"])

        self.registry.ingest_scan([], scan_revision="scan-2")
        self.assertEqual([], self.registry.search("地形材质"))

    def test_rescan_preserves_human_confirmation(self):
        record = {
            "object_path": "/Game/Meshes/SM_Bridge.SM_Bridge",
            "asset_name": "SM_Bridge",
            "asset_class": "/Script/Engine.StaticMesh",
        }
        self.registry.ingest_scan([record], scan_revision="scan-1")
        resource = self.registry.list_resources()[0]
        self.registry.update_confirmation(
            resource["id"],
            user_status="confirmed",
            description="人工确认的桥梁",
            purposes=["道路桥梁"],
        )
        self.registry.ingest_scan(
            [{**record, "description": "模型覆盖文本", "purposes": ["错误"]}],
            scan_revision="scan-2",
        )
        updated = self.registry.list_resources()[0]
        self.assertEqual("confirmed", updated["user_status"])
        self.assertEqual("人工确认的桥梁", updated["description"])


if __name__ == "__main__":
    unittest.main()

