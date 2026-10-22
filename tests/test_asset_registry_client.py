from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from asset_registry_client import AssetScanValidationError, build_asset_scan_command  # noqa: E402


class AssetRegistryClientTests(unittest.TestCase):
    def test_scan_command_contract(self):
        command = build_asset_scan_command(
            ["/Game", "/MyPlugin"], max_results=250, request_id="scan-test"
        )
        self.assertEqual("scan_project_assets", command["type"])
        self.assertEqual(["/Game", "/MyPlugin"], command["params"]["package_roots"])
        self.assertEqual(250, command["params"]["max_results"])
        self.assertEqual("scan-test", command["request_id"])

    def test_invalid_root_is_rejected_offline(self):
        with self.assertRaises(AssetScanValidationError):
            build_asset_scan_command(["Game"])


if __name__ == "__main__":
    unittest.main()

