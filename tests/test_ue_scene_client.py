from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from ue_protocol import UECapabilities  # noqa: E402
from ue_scene_client import (  # noqa: E402
    UESceneQueryError,
    query_scene_snapshot,
    validate_scene_query_capability,
)


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        return copy.deepcopy(self.pages.pop(0))


def capabilities() -> UECapabilities:
    return UECapabilities(
        contract_version=1,
        protocol_version="1.0",
        server="UnrealMCP",
        project_name="LandscapeTool",
        capabilities=("query_scene_state",),
        scene_query_supported_fields=("identity", "transform", "bounds", "tags"),
        scene_query_supported_filters=("actor_name",),
        scene_query_supports_pagination=True,
        scene_query_max_page_size=1000,
    )


class UESceneClientTests(unittest.TestCase):
    def setUp(self):
        self.query = {
            "query_id": "query_actor_smoke",
            "filter": {"actor_name": "LCA_ValidationActor"},
            "fields": ["identity", "transform", "bounds", "tags"],
            "page_size": 1,
        }

    def _page(self):
        page = fixture("fake_scene_before_page.json")
        page.update(
            {
                "query_id": "query_actor_smoke",
                "snapshot_token": "stable-token",
                "has_more": False,
                "next_cursor": None,
                "truncated": False,
            }
        )
        return page

    def test_query_builds_valid_snapshot(self):
        client = FakeClient([self._page()])

        snapshot = query_scene_snapshot(
            client,
            self.query,
            capabilities=capabilities(),
        )

        self.assertTrue(snapshot.completeness["complete"])
        self.assertEqual("LCA_ValidationActor", snapshot.entities[0]["name"])
        self.assertEqual("query_scene_state", client.calls[0][0])

    def test_changed_snapshot_token_rejects_cross_state_pages(self):
        first = self._page()
        first["entities"] = []
        first["total_count"] = 1
        first["has_more"] = True
        first["next_cursor"] = "1"
        second = self._page()
        second["snapshot_token"] = "changed-token"
        client = FakeClient([first, second])

        with self.assertRaisesRegex(UESceneQueryError, "场景发生变化"):
            query_scene_snapshot(client, self.query)

    def test_capability_precheck_rejects_unknown_field(self):
        invalid = dict(self.query)
        invalid["fields"] = ["properties"]

        with self.assertRaisesRegex(UESceneQueryError, "不支持场景字段"):
            validate_scene_query_capability(capabilities(), invalid)


if __name__ == "__main__":
    unittest.main()
