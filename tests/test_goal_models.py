from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from goal_compiler import GoalCompiler  # noqa: E402
from goal_models import GoalSpecError, goal_content_hash  # noqa: E402
from goal_store import GoalStore, GoalStoreError  # noqa: E402


def actor_x_document() -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / "actor_x_goal_draft.json").read_text(
            encoding="utf-8"
        )
    )


class GoalModelTests(unittest.TestCase):
    def test_fixed_actor_x_example_compiles_and_derives_exact_observation(self):
        goal = GoalCompiler.compile_document(
            "把测试 Actor 的 X 坐标设置为 100 厘米",
            actor_x_document(),
            goal_id="goal_actor_x_test",
        )

        constraint = goal.required_constraints[0]
        self.assertEqual("property_range", constraint["type"])
        self.assertEqual(
            ["transform.location.x"], constraint["required_observations"]
        )
        self.assertFalse(goal.approved)

    def test_unknown_constraint_subject_is_rejected(self):
        document = actor_x_document()
        document["required_constraints"][0]["subjects"] = ["missing_actor"]

        with self.assertRaises(GoalSpecError) as raised:
            GoalCompiler.compile_document("测试", document, goal_id="goal_unknown_subject")

        self.assertEqual("UNKNOWN_CONSTRAINT_SUBJECT", raised.exception.code)

    def test_invalid_property_range_is_rejected(self):
        document = actor_x_document()
        parameters = document["required_constraints"][0]["parameters"]
        parameters["minimum"] = 200
        parameters["maximum"] = 100

        with self.assertRaises(GoalSpecError) as raised:
            GoalCompiler.compile_document("测试", document, goal_id="goal_invalid_range")

        self.assertEqual("INVALID_PROPERTY_RANGE", raised.exception.code)


class GoalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = GoalStore("workspace-test", data_root=Path(self.temp_dir.name))
        self.goal = GoalCompiler.compile_document(
            "把测试 Actor 的 X 坐标设置为 100 厘米",
            actor_x_document(),
            goal_id="goal_store_test",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_approve_freezes_reviewed_hash(self):
        draft = self.store.save_draft(self.goal)
        expected_hash = goal_content_hash(draft.as_dict())
        approved = self.store.approve(
            draft.goal_id,
            approved_by="tester",
            expected_draft_hash=expected_hash,
        )

        self.assertTrue(approved.approved)
        self.assertEqual(expected_hash, approved.approval["draft_hash"])
        self.assertEqual(expected_hash, approved.approval["frozen_hash"])
        with self.assertRaisesRegex(GoalStoreError, "不能作为草案"):
            self.store.save_draft(approved)

    def test_stale_draft_hash_cannot_be_approved(self):
        draft = self.store.save_draft(self.goal)

        with self.assertRaisesRegex(GoalStoreError, "重新审核"):
            self.store.approve(
                draft.goal_id,
                approved_by="tester",
                expected_draft_hash="0" * 64,
            )

    def test_tampered_frozen_goal_is_rejected_when_reloaded(self):
        draft = self.store.save_draft(self.goal)
        approved = self.store.approve(
            draft.goal_id,
            approved_by="tester",
            expected_draft_hash=goal_content_hash(draft.as_dict()),
        )
        path = self.store.root / f"{approved.goal_id}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["required_constraints"][0]["parameters"]["maximum"] = 200
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        with self.assertRaises(GoalSpecError) as raised:
            self.store.get(approved.goal_id)
        self.assertEqual("GOAL_HASH_MISMATCH", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
