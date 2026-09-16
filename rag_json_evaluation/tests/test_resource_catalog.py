from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rag_json_evaluation.io_utils import load_jsonl
from rag_json_evaluation.resource_catalog import build_catalog


class ResourceCatalogTests(unittest.TestCase):
    def test_maps_content_file_to_candidate_ue_path_without_claiming_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "EnvModels"
            asset = root / "Roads" / "BP_TestRoad.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"package-header BlueprintGeneratedClass package-tail")
            output = Path(temporary) / "assets.jsonl"
            manifest = Path(temporary) / "manifest.json"
            result = build_catalog(root, output, manifest)
            record = load_jsonl(output)[0]

            self.assertEqual(result["file_count"], 1)
            self.assertEqual(record["ue_package_path"], "/Game/EnvModels/Roads/BP_TestRoad")
            self.assertEqual(record["ue_object_path_candidate"], "/Game/EnvModels/Roads/BP_TestRoad.BP_TestRoad")
            self.assertEqual(record["inferred_asset_type"], "blueprint_candidate")
            self.assertEqual(record["compatible_handlers"], ["RoadAuto"])
            self.assertEqual(record["compatible_path_fields"], ["actor_path"])
            self.assertEqual(record["ue_generated_class_path_candidate"], "/Game/EnvModels/Roads/BP_TestRoad.BP_TestRoad_C")
            self.assertEqual(record["preferred_plugin_path_candidate"], "/Game/EnvModels/Roads/BP_TestRoad.BP_TestRoad_C")
            self.assertEqual(record["verification_status"], "filesystem_verified_ue_unverified")


if __name__ == "__main__":
    unittest.main()
