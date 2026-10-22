from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from goal_compiler import GoalCompiler  # noqa: E402
from goal_models import GoalSpecError, goal_content_hash  # noqa: E402
from observation_planner import ObservationPlanError, ObservationPlanner  # noqa: E402


class FakeUEQueryCapabilities:
    def __init__(self, fields, supports_pagination=True):
        self.fields = fields
        self.supports_pagination = supports_pagination


class ObservationPlannerTests(unittest.TestCase):
    def setUp(self):
        document = json.loads(
            (REPO_ROOT / "contracts" / "examples" / "actor_x_goal_draft.json").read_text(
                encoding="utf-8"
            )
        )
        draft = GoalCompiler.compile_document(
            "把测试 Actor 的 X 坐标设置为 100 厘米",
            document,
            goal_id="goal_actor_x_plan",
        )
        # 观测计划默认只接受用户审核后的冻结目标。
        approved_document = draft.as_dict()
        frozen_hash = goal_content_hash(approved_document)
        approved_document["approval"] = {
            "status": "approved",
            "draft_hash": frozen_hash,
            "approved_at": "2026-10-22T00:00:00+00:00",
            "approved_by": "tester",
            "frozen_hash": frozen_hash,
        }
        self.goal = type(draft).from_dict(approved_document)
        self.planner = ObservationPlanner()
        self.fake_ue = json.loads(
            (REPO_ROOT / "contracts" / "examples" / "fake_ue_query_capabilities.json").read_text(
                encoding="utf-8"
            )
        )

    def test_same_goal_produces_stable_minimal_plan(self):
        first = self.planner.compile(self.goal)
        second = self.planner.compile(self.goal)

        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(["identity", "transform"], first.queries[0]["fields"])
        self.assertEqual(
            ["transform.location.x"],
            first.evidence_requirements[0]["required_fields"],
        )

    def test_draft_goal_cannot_be_used_for_execution_plan(self):
        draft_document = self.goal.as_dict()
        draft_document["approval"] = {
            "status": "draft",
            "draft_hash": None,
            "approved_at": None,
            "approved_by": None,
            "frozen_hash": None,
        }
        draft = type(self.goal).from_dict(draft_document)

        with self.assertRaises(ObservationPlanError) as raised:
            self.planner.compile(draft)
        self.assertEqual("GOAL_NOT_APPROVED", raised.exception.code)

    def test_fake_ue_missing_transform_is_explicitly_not_observable(self):
        plan = self.planner.compile(self.goal)
        fake_ue = FakeUEQueryCapabilities(fields=["identity", "class"])

        with self.assertRaises(ObservationPlanError) as raised:
            self.planner.validate_capabilities(
                plan,
                supported_fields=fake_ue.fields,
                supports_pagination=fake_ue.supports_pagination,
            )

        self.assertEqual("OBSERVATION_FIELD_UNSUPPORTED", raised.exception.code)
        self.assertEqual(["transform"], raised.exception.details["missing_fields"])

    def test_fake_ue_complete_capability_passes_preflight(self):
        plan = self.planner.compile(self.goal)

        self.planner.validate_capabilities(
            plan,
            supported_fields=self.fake_ue["supported_fields"],
            supports_pagination=self.fake_ue["supports_pagination"],
        )

    def test_modified_plan_id_is_rejected(self):
        plan = self.planner.compile(self.goal).as_dict()
        plan["queries"][0]["fields"].append("bounds")

        with self.assertRaises(GoalSpecError) as raised:
            type(self.planner.compile(self.goal)).from_dict(plan)
        self.assertEqual("QUERY_HASH_MISMATCH", raised.exception.code)

    def test_protected_entity_always_adds_before_after_observations(self):
        document = self.goal.as_dict()
        document["protected_entities"] = ["target_actor"]
        frozen_hash = goal_content_hash(document)
        document["approval"]["draft_hash"] = frozen_hash
        document["approval"]["frozen_hash"] = frozen_hash
        goal = type(self.goal).from_dict(document)

        plan = self.planner.compile(goal)

        protected = next(
            item
            for item in plan.evidence_requirements
            if item["constraint_id"] == "protected_unchanged_target_actor"
        )
        self.assertEqual(
            ["asset_reference", "bounds", "identity", "tags", "transform"],
            protected["required_fields"],
        )


if __name__ == "__main__":
    unittest.main()
