from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from resource_folder_report import (  # noqa: E402
    ResourceFolderReportStore,
    build_folder_summaries,
    build_resource_folder_report,
)
from resource_registry import ResourceRegistry  # noqa: E402


ASSETS = [
    {
        "object_path": "/Game/Env/Rocks/SM_Rock.SM_Rock",
        "package_path": "/Game/Env/Rocks",
        "asset_name": "SM_Rock",
        "asset_class": "/Script/Engine.StaticMesh",
    },
    {
        "object_path": "/Game/Env/Rocks/M_Rock.M_Rock",
        "package_path": "/Game/Env/Rocks",
        "asset_name": "M_Rock",
        "asset_class": "/Script/Engine.Material",
    },
    {
        "object_path": "/Game/Vehicles/BP_Truck.BP_Truck",
        "package_path": "/Game/Vehicles",
        "asset_name": "BP_Truck",
        "asset_class": "/Script/Engine.Blueprint",
    },
]


class FakeModelGateway:
    def chat(self, messages, *, profile_id=None):
        request = json.loads(messages[-1]["content"])
        folders = [
            {
                "folder_path": item["folder_path"],
                "purpose": f"用途 {item['folder_path']}",
                "asset_categories": ["测试资源"],
                "notes": "",
                "confidence": 0.9,
            }
            for item in request["folders_to_classify"]
        ]
        return SimpleNamespace(content=json.dumps({"folders": folders}, ensure_ascii=False))


class ResourceFolderReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_folder_summary_contains_every_level(self):
        summaries = build_folder_summaries(ASSETS)
        by_path = {item["folder_path"]: item for item in summaries}
        self.assertEqual(
            {"/Game", "/Game/Env", "/Game/Env/Rocks", "/Game/Vehicles"},
            set(by_path),
        )
        self.assertEqual(3, by_path["/Game"]["total_asset_count"])
        self.assertEqual(2, by_path["/Game/Env/Rocks"]["direct_asset_count"])

    def test_report_approval_applies_folder_semantics_and_writes_project_files(self):
        registry = ResourceRegistry("workspace-test", data_root=self.root / "data")
        ingest = registry.ingest_scan(ASSETS, scan_revision="scan-1")
        report = build_resource_folder_report(
            ASSETS,
            workspace_id="workspace-test",
            workspace_path=str(self.workspace),
            scan_revision=ingest["scan_revision"],
            model_gateway=FakeModelGateway(),
            profile_id="model-test",
            batch_size=2,
        )
        apply_result = registry.apply_folder_report(report)
        store = ResourceFolderReportStore(
            "workspace-test",
            str(self.workspace),
            data_root=self.root / "data",
        )
        store.save_draft(report)
        paths = store.approve(report)

        self.assertEqual(3, apply_result["confirmed"])
        self.assertEqual(0, apply_result["skipped"])
        self.assertTrue(Path(paths["project_json"]).is_file())
        self.assertTrue(Path(paths["project_markdown"]).is_file())
        self.assertEqual("approved", store.load_draft()["status"])
        matches = registry.search("/Game/Env/Rocks")
        self.assertEqual(2, len(matches))


if __name__ == "__main__":
    unittest.main()
