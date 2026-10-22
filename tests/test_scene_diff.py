from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from scene_diff import diff_snapshots  # noqa: E402
from scene_models import SceneSnapshotBuilder  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


class SceneDiffTests(unittest.TestCase):
    def setUp(self):
        self.builder = SceneSnapshotBuilder()
        self.query = {
            "filter": {"actor_name": "LCA_ValidationActor"},
            "fields": ["identity", "transform", "bounds", "tags"],
            "page_size": 200,
        }
        self.before = self.builder.build(
            self.query, [fixture("fake_scene_before_page.json")]
        )

    def test_static_scene_has_empty_comparable_diff(self):
        same = self.builder.build(
            self.query,
            [fixture("fake_scene_before_page.json")],
            captured_at="2026-10-22T10:00:00+00:00",
        )

        result = diff_snapshots(self.before, same)

        self.assertEqual("comparable", result.status)
        self.assertEqual((), result.modified_entities)
        self.assertEqual((), result.added_entities)
        self.assertEqual((), result.removed_entities)

    def test_one_actor_change_reports_only_changed_paths(self):
        after = self.builder.build(
            self.query, [fixture("fake_scene_after_page.json")]
        )

        result = diff_snapshots(self.before, after)

        self.assertEqual("comparable", result.status)
        self.assertEqual(1, len(result.modified_entities))
        paths = {
            item["path"] for item in result.modified_entities[0]["changes"]
        }
        self.assertEqual(
            {"bounds.origin.x", "transform.location.x"},
            paths,
        )

    def test_numeric_tolerance_suppresses_small_change(self):
        page = fixture("fake_scene_before_page.json")
        page["entities"][0]["transform"]["location"][0] = 0.05
        page["entities"][0]["bounds"]["origin"][0] = 0.05
        after = self.builder.build(self.query, [page])

        result = diff_snapshots(
            self.before,
            after,
            tolerances={"position_cm": 0.1},
        )

        self.assertEqual((), result.modified_entities)

    def test_incomplete_snapshot_is_inconclusive(self):
        page = fixture("fake_scene_before_page.json")
        page["truncated"] = True
        after = self.builder.build(self.query, [page])

        result = diff_snapshots(self.before, after)

        self.assertEqual("inconclusive", result.status)
        self.assertIn("after_snapshot_incomplete", result.reasons)


if __name__ == "__main__":
    unittest.main()
