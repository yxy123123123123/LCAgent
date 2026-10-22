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
from scene_models import SceneSnapshotBuilder  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def approved_goal(document: dict, goal_id: str = "goal_constraint_test") -> SceneGoalSpec:
    draft = GoalCompiler.compile_document("Actor 属性闭环测试", document, goal_id=goal_id)
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


class ConstraintEngineTests(unittest.TestCase):
    def setUp(self):
        self.goal_document = fixture("actor_x_goal_draft.json")
        self.goal = approved_goal(self.goal_document)
        self.query = {
            "filter": {"actor_name": "LCA_ValidationActor"},
            "fields": ["identity", "transform", "bounds", "tags"],
            "page_size": 200,
        }
        builder = SceneSnapshotBuilder()
        self.before = builder.build(
            self.query, [fixture("fake_scene_before_page.json")]
        )
        self.after = builder.build(
            self.query, [fixture("fake_scene_after_page.json")]
        )
        self.builder = builder
        self.engine = ConstraintEngine()

    def test_actor_x_range_fails_before_and_passes_after(self):
        failed = self.engine.validate(self.goal, self.before)
        passed = self.engine.validate(self.goal, self.after)

        self.assertEqual("failed", failed.overall_status)
        self.assertEqual("failed", failed.results[0]["status"])
        self.assertEqual("passed", passed.overall_status)
        self.assertEqual("passed", passed.results[0]["status"])
        self.assertIn("values", passed.results[0]["measured"])
        self.assertTrue(passed.results[0]["evidence_refs"])

    def test_missing_required_field_is_not_observable(self):
        page = fixture("fake_scene_after_page.json")
        page["entities"][0].pop("transform")
        page["missing_fields"] = ["transform"]
        snapshot = self.builder.build(self.query, [page])

        report = self.engine.validate(self.goal, snapshot)

        self.assertEqual("not_observable", report.overall_status)
        self.assertEqual("not_observable", report.results[0]["status"])

    def test_truncated_query_is_not_observable(self):
        page = fixture("fake_scene_after_page.json")
        page["truncated"] = True
        snapshot = self.builder.build(self.query, [page])

        report = self.engine.validate(self.goal, snapshot)

        self.assertEqual("not_observable", report.overall_status)

    def test_evaluator_exception_becomes_stable_error_result(self):
        document = fixture("actor_x_goal_draft.json")
        constraint = document["required_constraints"][0]
        constraint["parameters"] = {
            "property": "tags",
            "minimum": 0,
            "maximum": 10,
        }
        goal = approved_goal(document, "goal_error_result")

        report = self.engine.validate(goal, self.after)

        self.assertEqual("error", report.overall_status)
        self.assertEqual("error", report.results[0]["status"])
        self.assertIn("不是数值", report.results[0]["message"])

    def test_optional_failure_does_not_block_required_success(self):
        document = fixture("actor_x_goal_draft.json")
        document["optional_constraints"] = [
            {
                "constraint_id": "optional_wrong_name",
                "type": "property_equals",
                "subjects": ["target_actor"],
                "parameters": {"property": "name", "value": "WrongName"},
            }
        ]
        goal = approved_goal(document, "goal_optional_failure")

        report = self.engine.validate(goal, self.after)

        self.assertEqual("passed", report.overall_status)
        self.assertEqual("failed", report.results[1]["status"])
        self.assertFalse(report.results[1]["required"])

    def test_changed_protected_actor_blocks_success(self):
        document = fixture("actor_x_goal_draft.json")
        document["protected_entities"] = ["target_actor"]
        goal = approved_goal(document, "goal_protected_actor")

        report = self.engine.validate(
            goal,
            self.after,
            before_snapshot=self.before,
        )

        self.assertEqual("failed", report.overall_status)
        protected = next(
            item for item in report.results if item["constraint_type"] == "protected_unchanged"
        )
        self.assertEqual("failed", protected["status"])


if __name__ == "__main__":
    unittest.main()
