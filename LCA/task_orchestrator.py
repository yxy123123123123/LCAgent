"""执行、观测、验证和受限修复的通用任务编排器。

本模块不依赖 UE 实现。真实 UE、Fake UE 和离线回放只需提供相同的 observer、
action provider 与 executor 接口。模型可以提出动作，但不能产生最终成功状态。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator

from constraint_engine import ConstraintEngine, ValidationReport
from goal_models import ObservationPlan, SceneGoalSpec, canonical_hash, goal_content_hash
from observation_planner import ObservationPlanner
from repair_controller import RepairScope, RepairScopeController, RepairScopeError
from run_models import TerminationReason, normalize_budgets
from scene_diff import SceneDiff, diff_snapshots
from scene_models import SceneSnapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ACTION_PROPOSAL_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "orchestrator" / "action_proposal.schema.json"
)


class OrchestratorError(RuntimeError):
    """编排器输入或依赖实现错误。"""


class OrchestratorCancelledError(OrchestratorError):
    """用户取消任务。"""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    action_name: str
    phase: str
    declared_writes: tuple[dict[str, Any], ...]
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        action_name: str,
        phase: str,
        declared_writes: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any],
    ) -> "ActionProposal":
        body = {
            "action_name": action_name,
            "phase": phase,
            "declared_writes": _json_copy(list(declared_writes)),
            "payload": _json_copy(dict(payload)),
        }
        body["proposal_id"] = f"proposal_{canonical_hash(body)[:16]}"
        return cls.from_dict(body)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionProposal":
        document = _json_copy(dict(value))
        schema = json.loads(
            DEFAULT_ACTION_PROPOSAL_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            raise OrchestratorError(f"ActionProposal 不满足 Schema：{errors[0].message}")
        body = dict(document)
        body.pop("proposal_id")
        if document["proposal_id"] != f"proposal_{canonical_hash(body)[:16]}":
            raise OrchestratorError("ActionProposal 内容与 proposal_id 不一致")
        return cls(
            proposal_id=document["proposal_id"],
            action_name=document["action_name"],
            phase=document["phase"],
            declared_writes=tuple(document["declared_writes"]),
            payload=document["payload"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action_name": self.action_name,
            "phase": self.phase,
            "declared_writes": _json_copy(self.declared_writes),
            "payload": _json_copy(self.payload),
        }


@dataclass(frozen=True)
class ActionExecutionResult:
    success: bool
    observation: dict[str, Any]
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "observation": _json_copy(self.observation),
            "error": _json_copy(self.error),
        }


class SceneObserver(Protocol):
    def capture(self, plan: ObservationPlan) -> SceneSnapshot: ...


class ActionExecutor(Protocol):
    def execute(self, proposal: ActionProposal) -> ActionExecutionResult: ...


class ActionProvider(Protocol):
    def propose_initial(
        self,
        goal: SceneGoalSpec,
        scope: RepairScope,
    ) -> ActionProposal | None: ...

    def propose_execution_repair(
        self,
        goal: SceneGoalSpec,
        failed_proposal: ActionProposal,
        result: ActionExecutionResult,
        scope: RepairScope,
    ) -> ActionProposal | None: ...

    def propose_goal_repair(
        self,
        goal: SceneGoalSpec,
        report: ValidationReport,
        scope: RepairScope,
    ) -> ActionProposal | None: ...


@dataclass(frozen=True)
class OrchestrationResult:
    goal_id: str
    status: str
    termination_reason: str
    snapshots: tuple[dict[str, Any], ...]
    validation_reports: tuple[dict[str, Any], ...]
    scene_diffs: tuple[dict[str, Any], ...]
    repair_scopes: tuple[dict[str, Any], ...]
    action_records: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "status": self.status,
            "termination_reason": self.termination_reason,
            "snapshots": _json_copy(self.snapshots),
            "validation_reports": _json_copy(self.validation_reports),
            "scene_diffs": _json_copy(self.scene_diffs),
            "repair_scopes": _json_copy(self.repair_scopes),
            "action_records": _json_copy(self.action_records),
            "metrics": _json_copy(self.metrics),
            "error": _json_copy(self.error),
        }


class TaskOrchestrator:
    def __init__(
        self,
        *,
        observer: SceneObserver,
        action_provider: ActionProvider,
        executor: ActionExecutor,
        constraint_engine: ConstraintEngine | None = None,
        repair_controller: RepairScopeController | None = None,
        event_callback: Callable[[str, Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.observer = observer
        self.action_provider = action_provider
        self.executor = executor
        self.constraint_engine = constraint_engine or ConstraintEngine()
        self.repair_controller = repair_controller or RepairScopeController()
        self.event_callback = event_callback
        self.should_cancel = should_cancel or (lambda: False)
        self.clock = clock

    def _emit(self, event_type: str, **summary: Any) -> None:
        if self.event_callback is not None:
            self.event_callback(event_type, summary)

    def _check_limits(
        self,
        *,
        started_at: float,
        budgets: Mapping[str, int],
        metrics: Mapping[str, int],
    ) -> None:
        if self.should_cancel():
            raise OrchestratorCancelledError("运行已取消")
        if self.clock() - started_at > budgets["max_elapsed_seconds"]:
            raise OrchestratorError("运行时间预算已耗尽")
        if metrics["model_steps"] >= budgets["max_model_steps"]:
            raise OrchestratorError("模型步骤预算已耗尽")

    def _provider_call(
        self,
        method_name: str,
        *args: Any,
        started_at: float,
        budgets: Mapping[str, int],
        metrics: dict[str, int],
    ) -> ActionProposal | None:
        self._check_limits(started_at=started_at, budgets=budgets, metrics=metrics)
        metrics["model_steps"] += 1
        proposal = getattr(self.action_provider, method_name)(*args)
        return ActionProposal.from_dict(proposal.as_dict()) if proposal is not None else None

    def _execute_with_repair(
        self,
        goal: SceneGoalSpec,
        proposal: ActionProposal,
        scope: RepairScope,
        *,
        started_at: float,
        budgets: Mapping[str, int],
        metrics: dict[str, int],
        records: list[dict[str, Any]],
    ) -> ActionExecutionResult:
        execution_repairs = 0
        current = proposal
        while True:
            if self.should_cancel():
                raise OrchestratorCancelledError("运行已取消")
            if metrics["tool_actions"] >= budgets["max_tool_actions"]:
                raise OrchestratorError("工具动作预算已耗尽")
            self.repair_controller.validate_declared_writes(
                scope, current.declared_writes
            )
            result = self.executor.execute(current)
            metrics["tool_actions"] += 1
            records.append(
                {
                    "proposal": current.as_dict(),
                    "result": result.as_dict(),
                    "execution_repair_index": execution_repairs,
                }
            )
            self._emit(
                "action_executed",
                proposal_id=current.proposal_id,
                success=result.success,
                execution_repair_index=execution_repairs,
            )
            if result.success:
                return result
            if execution_repairs >= budgets["max_execution_repairs"]:
                raise OrchestratorError("执行修复预算已耗尽")
            replacement = self._provider_call(
                "propose_execution_repair",
                goal,
                current,
                result,
                scope,
                started_at=started_at,
                budgets=budgets,
                metrics=metrics,
            )
            execution_repairs += 1
            metrics["execution_repairs"] += 1
            if replacement is None:
                raise OrchestratorError("执行失败且模型没有给出可验证的修复动作")
            current = replacement

    @staticmethod
    def _termination_for_report(report: ValidationReport) -> str:
        if report.overall_status == "not_observable":
            return TerminationReason.NOT_OBSERVABLE.value
        if report.overall_status == "passed":
            return TerminationReason.GOAL_SATISFIED.value
        return TerminationReason.CONSTRAINT_FAILED.value

    def run(
        self,
        goal: SceneGoalSpec,
        *,
        plan: ObservationPlan | None = None,
        budgets: Mapping[str, Any] | None = None,
    ) -> OrchestrationResult:
        goal = SceneGoalSpec.from_dict(goal.as_dict())
        if not goal.approved:
            raise OrchestratorError("只有审核冻结的目标才能执行")
        plan = plan or ObservationPlanner().compile(goal)
        plan = ObservationPlan.from_dict(plan.as_dict())
        if plan.goal_id != goal.goal_id or plan.goal_hash != goal_content_hash(goal.as_dict()):
            raise OrchestratorError("观测计划与冻结目标不匹配")
        run_budgets = normalize_budgets(budgets)
        started_at = self.clock()
        metrics = {
            "model_steps": 0,
            "tool_actions": 0,
            "execution_repairs": 0,
            "goal_repairs": 0,
        }
        snapshots: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        diffs: list[dict[str, Any]] = []
        scopes: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []

        def finish(
            status: str,
            reason: str,
            *,
            error: Mapping[str, Any] | None = None,
        ) -> OrchestrationResult:
            metrics["elapsed_seconds"] = max(0.0, self.clock() - started_at)
            self._emit("orchestration_finished", status=status, termination_reason=reason)
            return OrchestrationResult(
                goal_id=goal.goal_id,
                status=status,
                termination_reason=reason,
                snapshots=tuple(snapshots),
                validation_reports=tuple(reports),
                scene_diffs=tuple(diffs),
                repair_scopes=tuple(scopes),
                action_records=tuple(records),
                metrics=dict(metrics),
                error=dict(error) if error else None,
            )

        try:
            self._emit("capture_before_started", goal_id=goal.goal_id)
            baseline = SceneSnapshot.from_dict(self.observer.capture(plan).as_dict())
            snapshots.append(baseline.as_dict())
            initial_scope = self.repair_controller.derive_initial(
                goal,
                baseline,
                remaining_tool_actions=run_budgets["max_tool_actions"],
            )
            scopes.append(initial_scope.as_dict())
            proposal = self._provider_call(
                "propose_initial",
                goal,
                initial_scope,
                started_at=started_at,
                budgets=run_budgets,
                metrics=metrics,
            )
            current = baseline
            if proposal is not None:
                self._execute_with_repair(
                    goal,
                    proposal,
                    initial_scope,
                    started_at=started_at,
                    budgets=run_budgets,
                    metrics=metrics,
                    records=records,
                )
                current = SceneSnapshot.from_dict(self.observer.capture(plan).as_dict())
                snapshots.append(current.as_dict())
                initial_diff = diff_snapshots(
                    baseline, current, tolerances=goal.tolerances
                )
                self.repair_controller.validate_observed_diff(
                    initial_scope, initial_diff
                )
                diffs.append(initial_diff.as_dict())

            report = self.constraint_engine.validate(
                goal,
                current,
                before_snapshot=baseline,
            )
            reports.append(report.as_dict())
            self._emit(
                "scene_validated",
                report_id=report.report_id,
                overall_status=report.overall_status,
            )
            if report.overall_status == "passed":
                return finish("succeeded", TerminationReason.GOAL_SATISFIED.value)
            if report.overall_status in {"not_observable", "error"}:
                return finish("failed", self._termination_for_report(report))

            while report.overall_status == "failed":
                if metrics["goal_repairs"] >= run_budgets["max_goal_repairs"]:
                    return finish("failed", TerminationReason.BUDGET_EXHAUSTED.value)
                remaining_repairs = run_budgets["max_goal_repairs"] - metrics["goal_repairs"]
                remaining_actions = run_budgets["max_tool_actions"] - metrics["tool_actions"]
                scope = self.repair_controller.derive(
                    goal,
                    report,
                    current,
                    remaining_goal_repairs=remaining_repairs,
                    remaining_tool_actions=remaining_actions,
                )
                scopes.append(scope.as_dict())
                repair = self._provider_call(
                    "propose_goal_repair",
                    goal,
                    report,
                    scope,
                    started_at=started_at,
                    budgets=run_budgets,
                    metrics=metrics,
                )
                if repair is None:
                    return finish("failed", TerminationReason.CONSTRAINT_FAILED.value)
                previous = current
                self._execute_with_repair(
                    goal,
                    repair,
                    scope,
                    started_at=started_at,
                    budgets=run_budgets,
                    metrics=metrics,
                    records=records,
                )
                metrics["goal_repairs"] += 1
                current = SceneSnapshot.from_dict(self.observer.capture(plan).as_dict())
                snapshots.append(current.as_dict())
                scene_diff = diff_snapshots(
                    previous, current, tolerances=goal.tolerances
                )
                self.repair_controller.validate_observed_diff(scope, scene_diff)
                diffs.append(scene_diff.as_dict())
                report = self.constraint_engine.validate(
                    goal,
                    current,
                    before_snapshot=baseline,
                )
                reports.append(report.as_dict())
                self._emit(
                    "scene_revalidated",
                    report_id=report.report_id,
                    overall_status=report.overall_status,
                    goal_repair_index=metrics["goal_repairs"],
                )
                if report.overall_status == "passed":
                    return finish("succeeded", TerminationReason.GOAL_SATISFIED.value)
                if report.overall_status in {"not_observable", "error"}:
                    return finish("failed", self._termination_for_report(report))

            return finish("failed", TerminationReason.CONSTRAINT_FAILED.value)
        except OrchestratorCancelledError as exc:
            return finish(
                "cancelled",
                TerminationReason.CANCELLED.value,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        except RepairScopeError as exc:
            return finish(
                "failed",
                TerminationReason.CONSTRAINT_FAILED.value,
                error={"type": type(exc).__name__, "code": exc.code, "message": str(exc)},
            )
        except Exception as exc:
            message = str(exc)
            reason = (
                TerminationReason.BUDGET_EXHAUSTED.value
                if "预算已耗尽" in message
                else TerminationReason.TOOL_ERROR.value
            )
            return finish(
                "failed",
                reason,
                error={"type": type(exc).__name__, "message": message},
            )
