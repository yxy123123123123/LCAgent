from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "LCA"))

from goal_compiler import GoalCompiler  # noqa: E402
from goal_models import SceneGoalSpec, goal_content_hash  # noqa: E402
from observation_planner import ObservationPlanner  # noqa: E402
from scene_models import SceneSnapshotBuilder  # noqa: E402
from task_orchestrator import (  # noqa: E402
    ActionExecutionResult,
    ActionProposal,
    TaskOrchestrator,
)


def fixture(name: str) -> dict:
    return json.loads(
        (REPO_ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def approved_actor_goal() -> SceneGoalSpec:
    draft = GoalCompiler.compile_document(
        "将测试 Actor 的 X 坐标设置为 100 厘米",
        fixture("actor_x_goal_draft.json"),
        goal_id="goal_fake_ue_closed_loop",
    )
    document = draft.as_dict()
    frozen_hash = goal_content_hash(document)
    document["approval"] = {
        "status": "approved",
        "draft_hash": frozen_hash,
        "approved_at": "2026-10-22T00:00:00+00:00",
        "approved_by": "tester",
        "frozen_hash": frozen_hash,
    }
    return SceneGoalSpec.from_dict(document)


def proposal(x: float, phase: str, *, property_path: str = "transform.location.x"):
    return ActionProposal.create(
        action_name="set_actor_property",
        phase=phase,
        declared_writes=[
            {
                "entity_id": "target_actor",
                "operation": "update",
                "properties": [property_path],
            }
        ],
        payload={"property": property_path, "value": x},
    )


class FakeUEScene:
    def __init__(self, x: float = 0.0, *, truncated: bool = False):
        self.x = x
        self.truncated = truncated
        self.capture_count = 0

    def capture(self, plan):
        self.capture_count += 1
        page = fixture("fake_scene_before_page.json")
        page["entities"][0]["transform"]["location"][0] = self.x
        page["entities"][0]["bounds"]["origin"][0] = self.x
        page["truncated"] = self.truncated
        query = plan.queries[0]
        return SceneSnapshotBuilder().build(
            query,
            [page],
            captured_at=f"2026-10-22T00:00:0{self.capture_count}+00:00",
        )


class FakeExecutor:
    def __init__(self, scene: FakeUEScene, *, fail_first: bool = False):
        self.scene = scene
        self.fail_first = fail_first
        self.calls = []

    def execute(self, action):
        self.calls.append(action)
        if self.fail_first:
            self.fail_first = False
            return ActionExecutionResult(
                success=False,
                observation={},
                error={"code": "FAKE_EXECUTION_ERROR"},
            )
        if action.payload["property"] == "transform.location.x":
            self.scene.x = float(action.payload["value"])
        return ActionExecutionResult(
            success=True,
            observation={"status": "success"},
        )


class FakeProvider:
    def __init__(
        self,
        *,
        initial_x: float | None = 50.0,
        repair_x: float | None = 100.0,
        repair_property: str = "transform.location.x",
    ):
        self.initial_x = initial_x
        self.repair_x = repair_x
        self.repair_property = repair_property
        self.calls = []

    def propose_initial(self, goal, scope):
        self.calls.append("initial")
        if self.initial_x is None:
            return None
        return proposal(self.initial_x, "initial")

    def propose_execution_repair(self, goal, failed_proposal, result, scope):
        self.calls.append("execution_repair")
        return proposal(self.initial_x or 50.0, "execution_repair")

    def propose_goal_repair(self, goal, report, scope):
        self.calls.append("goal_repair")
        if self.repair_x is None:
            return None
        return proposal(
            self.repair_x,
            "goal_repair",
            property_path=self.repair_property,
        )


class TaskOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.goal = approved_actor_goal()
        self.plan = ObservationPlanner().compile(self.goal)

    def _orchestrator(self, scene, provider, executor=None, **kwargs):
        return TaskOrchestrator(
            observer=scene,
            action_provider=provider,
            executor=executor or FakeExecutor(scene),
            **kwargs,
        )

    def test_fake_ue_fails_then_one_scoped_repair_passes(self):
        scene = FakeUEScene()
        provider = FakeProvider(initial_x=50, repair_x=100)
        executor = FakeExecutor(scene)

        result = self._orchestrator(scene, provider, executor).run(
            self.goal,
            plan=self.plan,
        )

        self.assertEqual("succeeded", result.status)
        self.assertEqual("goal_satisfied", result.termination_reason)
        self.assertEqual(1, result.metrics["goal_repairs"])
        self.assertEqual(2, result.metrics["tool_actions"])
        self.assertEqual(
            ["failed", "passed"],
            [item["overall_status"] for item in result.validation_reports],
        )

    def test_out_of_scope_repair_is_rejected_before_execution(self):
        scene = FakeUEScene()
        provider = FakeProvider(
            initial_x=50,
            repair_x=100,
            repair_property="transform.location.y",
        )
        executor = FakeExecutor(scene)

        result = self._orchestrator(scene, provider, executor).run(self.goal, plan=self.plan)

        self.assertEqual("failed", result.status)
        self.assertEqual("constraint_failed", result.termination_reason)
        self.assertEqual("PROPERTY_WRITE_OUT_OF_SCOPE", result.error["code"])
        self.assertEqual(1, len(executor.calls))

    def test_goal_repair_loop_stops_at_budget(self):
        scene = FakeUEScene()
        provider = FakeProvider(initial_x=50, repair_x=50)

        result = self._orchestrator(scene, provider).run(
            self.goal,
            plan=self.plan,
            budgets={"max_goal_repairs": 1},
        )

        self.assertEqual("failed", result.status)
        self.assertEqual("budget_exhausted", result.termination_reason)
        self.assertEqual(1, result.metrics["goal_repairs"])

    def test_model_returning_no_action_cannot_bypass_validation(self):
        scene = FakeUEScene()
        provider = FakeProvider(initial_x=None, repair_x=None)

        result = self._orchestrator(scene, provider).run(self.goal, plan=self.plan)

        self.assertEqual("failed", result.status)
        self.assertEqual("constraint_failed", result.termination_reason)
        self.assertEqual("failed", result.validation_reports[-1]["overall_status"])

    def test_incomplete_fake_ue_query_terminates_not_observable(self):
        scene = FakeUEScene(truncated=True)
        provider = FakeProvider(initial_x=None, repair_x=100)

        result = self._orchestrator(scene, provider).run(self.goal, plan=self.plan)

        self.assertEqual("failed", result.status)
        self.assertEqual("not_observable", result.termination_reason)

    def test_execution_repair_budget_is_separate_from_goal_repair(self):
        scene = FakeUEScene()
        provider = FakeProvider(initial_x=50, repair_x=100)
        executor = FakeExecutor(scene, fail_first=True)

        result = self._orchestrator(scene, provider, executor).run(self.goal, plan=self.plan)

        self.assertEqual("succeeded", result.status)
        self.assertEqual(1, result.metrics["execution_repairs"])
        self.assertEqual(1, result.metrics["goal_repairs"])
        self.assertIn("execution_repair", provider.calls)

    def test_cancelled_run_does_not_call_model_or_executor(self):
        scene = FakeUEScene()
        provider = FakeProvider()
        executor = FakeExecutor(scene)

        result = self._orchestrator(
            scene,
            provider,
            executor,
            should_cancel=lambda: True,
        ).run(self.goal, plan=self.plan)

        self.assertEqual("cancelled", result.status)
        self.assertEqual([], provider.calls)
        self.assertEqual([], executor.calls)


if __name__ == "__main__":
    unittest.main()
