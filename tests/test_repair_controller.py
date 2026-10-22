from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from constraint_engine import ConstraintEngine  # noqa: E402
from goal_compiler import GoalCompiler  # noqa: E402
from goal_models import SceneGoalSpec, goal_content_hash  # noqa: E402
from repair_controller import RepairScopeController, RepairScopeError  # noqa: E402
from scene_diff import diff_snapshots  # noqa: E402
from scene_models import SceneSnapshotBuilder  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def approve(document: dict, goal_id: str) -> SceneGoalSpec:
    draft = GoalCompiler.compile_document("修复范围测试", document, goal_id=goal_id)
    value = draft.as_dict()
    frozen_hash = goal_content_hash(value)
    value["approval"] = {
        "status": "approved",
        "draft_hash": frozen_hash,
        "approved_at": "2026-10-22T00:00:00+00:00",
        "approved_by": "tester",
        "frozen_hash": frozen_hash,
    }
    return SceneGoalSpec.from_dict(value)


class RepairScopeTests(unittest.TestCase):
    def setUp(self):
        document = fixture("actor_x_goal_draft.json")
        self.goal = approve(document, "goal_repair_scope")
        query = {
            "filter": {"actor_name": "LCA_ValidationActor"},
            "fields": ["identity", "transform", "bounds", "tags"],
            "page_size": 200,
        }
        builder = SceneSnapshotBuilder()
        self.before = builder.build(query, [fixture("fake_scene_before_page.json")])
        self.after = builder.build(query, [fixture("fake_scene_after_page.json")])
        self.report = ConstraintEngine().validate(self.goal, self.before)
        self.controller = RepairScopeController()
        self.scope = self.controller.derive(
            self.goal,
            self.report,
            self.before,
            remaining_goal_repairs=2,
            remaining_tool_actions=8,
        )

    def test_scope_is_derived_from_failed_constraint(self):
        self.assertEqual(("actor_x_in_range",), self.scope.failed_constraints)
        self.assertEqual(("target_actor",), self.scope.allowed_entities)
        self.assertEqual(("update",), self.scope.allowed_operations)
        self.assertEqual(("transform.location.x",), self.scope.allowed_properties)
        self.assertEqual(
            ("bounds.origin.x", "transform.location.x"),
            self.scope.allowed_observed_paths,
        )

    def test_declared_write_is_checked_before_execution(self):
        self.controller.validate_declared_writes(
            self.scope,
            [
                {
                    "entity_id": "target_actor",
                    "operation": "update",
                    "properties": ["transform.location.x"],
                }
            ],
        )

        with self.assertRaises(RepairScopeError) as raised:
            self.controller.validate_declared_writes(
                self.scope,
                [
                    {
                        "entity_id": "target_actor",
                        "operation": "update",
                        "properties": ["transform.location.y"],
                    }
                ],
            )
        self.assertEqual("PROPERTY_WRITE_OUT_OF_SCOPE", raised.exception.code)

    def test_expected_real_diff_is_allowed(self):
        scene_diff = diff_snapshots(self.before, self.after)
        self.controller.validate_observed_diff(self.scope, scene_diff)

    def test_undeclared_real_property_change_is_rejected(self):
        page = fixture("fake_scene_after_page.json")
        page["entities"][0]["transform"]["location"][1] = 25
        page["entities"][0]["bounds"]["origin"][1] = 25
        changed = SceneSnapshotBuilder().build(self.before.query, [page])
        scene_diff = diff_snapshots(self.before, changed)

        with self.assertRaises(RepairScopeError) as raised:
            self.controller.validate_observed_diff(self.scope, scene_diff)
        self.assertEqual("OBSERVED_PROPERTY_OUT_OF_SCOPE", raised.exception.code)

    def test_failed_constraint_cannot_authorize_protected_entity(self):
        document = fixture("actor_x_goal_draft.json")
        document["protected_entities"] = ["target_actor"]
        goal = approve(document, "goal_protected_scope")
        report = ConstraintEngine().validate(
            goal,
            self.before,
            before_snapshot=self.before,
        )

        with self.assertRaises(RepairScopeError) as raised:
            self.controller.derive(
                goal,
                report,
                self.before,
                remaining_goal_repairs=2,
                remaining_tool_actions=8,
            )
        self.assertEqual("PROTECTED_ENTITY_REPAIR", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
