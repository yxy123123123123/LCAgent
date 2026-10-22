from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from scene_models import SceneModelError, SceneSnapshot, SceneSnapshotBuilder  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


class SceneSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.builder = SceneSnapshotBuilder()
        self.query = {
            "filter": {"actor_name": "LCA_ValidationActor"},
            "fields": ["identity", "transform", "bounds", "tags"],
            "page_size": 200,
        }

    def test_static_scene_hash_does_not_depend_on_capture_time(self):
        page = fixture("fake_scene_before_page.json")
        first = self.builder.build(
            self.query, [page], captured_at="2026-10-22T00:00:00+00:00"
        )
        second = self.builder.build(
            self.query, [page], captured_at="2026-10-22T01:00:00+00:00"
        )

        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertNotEqual(first.captured_at, second.captured_at)
        self.assertIn("transform.location.x", first.entities[0]["available_fields"])

    def test_missing_field_is_not_filled_with_fake_default(self):
        page = fixture("fake_scene_before_page.json")
        page["entities"][0].pop("transform")
        page["missing_fields"] = ["transform"]

        snapshot = self.builder.build(self.query, [page])

        self.assertFalse(snapshot.completeness["complete"])
        self.assertNotIn("transform", snapshot.entities[0])
        self.assertNotIn("transform.location.x", snapshot.entities[0]["available_fields"])

    def test_unfinished_pagination_marks_snapshot_incomplete(self):
        page = fixture("fake_scene_before_page.json")
        page["next_cursor"] = "page-2"
        page["total_count"] = 2

        snapshot = self.builder.build(self.query, [page])

        self.assertFalse(snapshot.completeness["complete"])
        self.assertTrue(snapshot.completeness["truncated"])

    def test_conflicting_duplicate_entity_across_pages_is_rejected(self):
        first = fixture("fake_scene_before_page.json")
        second = fixture("fake_scene_before_page.json")
        first["total_count"] = 1
        second["total_count"] = 1
        second["entities"][0]["name"] = "ChangedName"

        with self.assertRaises(SceneModelError) as raised:
            self.builder.build(self.query, [first, second])
        self.assertEqual("DUPLICATE_ENTITY_CONFLICT", raised.exception.code)

    def test_snapshot_hash_detects_manual_change(self):
        snapshot = self.builder.build(self.query, [fixture("fake_scene_before_page.json")])
        document = snapshot.as_dict()
        document["entities"][0]["transform"]["location"]["x"] = 50

        with self.assertRaises(SceneModelError) as raised:
            SceneSnapshot.from_dict(document)
        self.assertEqual("SNAPSHOT_HASH_MISMATCH", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
