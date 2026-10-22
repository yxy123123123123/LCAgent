"""后台运行管理器。

M2 阶段复用现有 DecisionAgent，只把同步调用包装为可持久化、可取消的运行。
后续 TaskOrchestrator 将在不改变 Service API 的前提下替换这里的执行核心。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping

from Agent import AgentCancelledError, DecisionAgent, StateMachineError, TraceStep
from goal_models import ObservationPlan, SceneGoalSpec, goal_content_hash
from run_models import (
    RunStatus,
    TaskRun,
    TerminationReason,
    new_run_id,
    normalize_budgets,
    utc_now,
)
from run_store import RunStore, RunStoreError


class RunManagerError(RuntimeError):
    """运行创建、等待或取消错误。"""


class RunManager:
    def __init__(
        self,
        run_store: RunStore | None = None,
        *,
        agent_factory: Callable[..., Any] = DecisionAgent,
        max_workers: int = 2,
    ) -> None:
        self.run_store = run_store or RunStore()
        self.agent_factory = agent_factory
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="lcagent-run",
        )
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        *,
        workspace_id: str,
        workspace_path: str,
        task_text: str,
        model_profile_id: str | None = None,
        embedding_profile_id: str | None = None,
        dry_run: bool = True,
        budgets: Mapping[str, Any] | None = None,
    ) -> TaskRun:
        if not workspace_id.strip() or not task_text.strip():
            raise RunManagerError("workspace_id 和 task 不能为空")
        if not Path(workspace_path).is_dir():
            raise RunManagerError(f"工作区目录不存在：{workspace_path}")
        normalized_budgets = normalize_budgets(budgets)
        run = TaskRun(
            run_id=new_run_id(),
            workspace_id=workspace_id,
            task_text=task_text.strip(),
            model_profile_id=model_profile_id,
            embedding_profile_id=embedding_profile_id,
            execution_mode="dry_run" if dry_run else "live",
            budgets=normalized_budgets,
        )
        self.run_store.create_run(run)
        self.run_store.append_event(
            run.run_id,
            event_type="run_created",
            phase="QUEUED",
            status="success",
            summary={"execution_mode": run.execution_mode},
        )
        future = self.executor.submit(self._execute, run.run_id, workspace_path)
        with self._lock:
            self._futures[run.run_id] = future
        future.add_done_callback(
            lambda completed, current_run_id=run.run_id: self._forget_future(
                current_run_id, completed
            )
        )
        return run

    def submit_orchestrated(
        self,
        *,
        workspace_id: str,
        workspace_path: str,
        goal: SceneGoalSpec,
        plan: ObservationPlan,
        orchestrator_factory: Callable[
            [Callable[[], bool], Callable[[str, Mapping[str, Any]], None]],
            Any,
        ],
        model_profile_id: str | None = None,
        embedding_profile_id: str | None = None,
        budgets: Mapping[str, Any] | None = None,
    ) -> TaskRun:
        """提交强验证任务；真实 UE 与 Fake UE 通过工厂注入相同接口。"""

        if not Path(workspace_path).is_dir():
            raise RunManagerError(f"工作区目录不存在：{workspace_path}")
        goal = SceneGoalSpec.from_dict(goal.as_dict())
        plan = ObservationPlan.from_dict(plan.as_dict())
        if (
            not workspace_id.strip()
            or not goal.approved
            or plan.goal_id != goal.goal_id
            or plan.goal_hash != goal_content_hash(goal.as_dict())
        ):
            raise RunManagerError("强验证运行要求已冻结且匹配的目标与观测计划")
        normalized_budgets = normalize_budgets(budgets)
        run = TaskRun(
            run_id=new_run_id(),
            workspace_id=workspace_id,
            task_text=goal.task_text,
            model_profile_id=model_profile_id,
            embedding_profile_id=embedding_profile_id,
            execution_mode="live",
            budgets=normalized_budgets,
        )
        self.run_store.create_run(run)
        self.run_store.append_event(
            run.run_id,
            event_type="orchestration_created",
            phase="PREPARE",
            status="success",
            summary={"goal_id": goal.goal_id, "plan_id": plan.plan_id},
        )
        future = self.executor.submit(
            self._execute_orchestrated,
            run.run_id,
            goal,
            plan,
            orchestrator_factory,
        )
        with self._lock:
            self._futures[run.run_id] = future
        future.add_done_callback(
            lambda completed, current_run_id=run.run_id: self._forget_future(
                current_run_id, completed
            )
        )
        return run

    def _forget_future(self, run_id: str, future: Future[Any]) -> None:
        with self._lock:
            if self._futures.get(run_id) is future:
                self._futures.pop(run_id, None)

    def _trace_callback(self, run_id: str) -> Callable[[TraceStep], None]:
        def callback(step: TraceStep) -> None:
            if step.state == "REPAIR":
                self.run_store.update_run(run_id, status=RunStatus.REPAIRING.value)
            elif step.state == "ANALYZE":
                current = self.run_store.get_run(run_id)
                if current.status == RunStatus.REPAIRING.value:
                    self.run_store.update_run(run_id, status=RunStatus.RUNNING.value)
            self.run_store.append_event(
                run_id,
                event_type="agent_trace",
                phase=step.state,
                status=step.status,
                summary={"action": step.action, "detail": step.detail},
            )

        return callback

    def _finish_cancelled(self, run_id: str) -> None:
        current = self.run_store.get_run(run_id)
        if current.status == RunStatus.CANCELLED.value:
            return
        self.run_store.update_run(
            run_id,
            status=RunStatus.CANCELLED.value,
            termination_reason=TerminationReason.CANCELLED.value,
            finished_at=utc_now(),
            error=None,
        )
        self.run_store.append_event(
            run_id,
            event_type="run_cancelled",
            phase="CANCELLED",
            status="success",
            summary={},
        )

    def _execute(self, run_id: str, workspace_path: str) -> dict[str, Any] | None:
        try:
            if self.run_store.is_cancel_requested(run_id):
                self._finish_cancelled(run_id)
                return None
            run = self.run_store.update_run(
                run_id,
                status=RunStatus.RUNNING.value,
                started_at=utc_now(),
            )
            self.run_store.append_event(
                run_id,
                event_type="run_started",
                phase="ANALYZE",
                status="success",
                summary={},
            )
            budgets = dict(run.budgets)
            agent = self.agent_factory(
                project_directory=workspace_path,
                workspace_id=run.workspace_id,
                model_profile_id=run.model_profile_id,
                embedding_profile_id=run.embedding_profile_id,
                dry_run=run.execution_mode == "dry_run",
                max_model_steps=budgets["max_model_steps"],
                max_repairs=budgets["max_execution_repairs"],
                should_cancel=lambda: self.run_store.is_cancel_requested(run_id),
                trace_callback=self._trace_callback(run_id),
            )
            result = agent.run_with_trace(run.task_text)
            if self.run_store.is_cancel_requested(run_id):
                self._finish_cancelled(run_id)
                return None
            result_document = result.as_dict()
            artifact = self.run_store.write_json_artifact(
                run_id, "agent_result.json", result_document
            )
            reason = (
                TerminationReason.DRY_RUN_COMPLETED.value
                if result.dry_run
                else TerminationReason.MODEL_FINISHED_WITHOUT_VERIFICATION.value
            )
            self.run_store.update_run(
                run_id,
                status=RunStatus.SUCCEEDED.value,
                termination_reason=reason,
                finished_at=utc_now(),
                final_answer=result.final_answer,
                metrics={"model_steps": result.model_steps, "dry_run": result.dry_run},
            )
            self.run_store.append_event(
                run_id,
                event_type="run_completed",
                phase="FINISH",
                status="success",
                summary={"termination_reason": reason},
                artifact_refs=[artifact["relative_path"]],
            )
            return result_document
        except AgentCancelledError:
            self._finish_cancelled(run_id)
            return None
        except Exception as exc:
            message = str(exc)
            reason = (
                TerminationReason.BUDGET_EXHAUSTED.value
                if isinstance(exc, StateMachineError)
                and ("最大模型步数" in message or "修复次数超过" in message)
                else TerminationReason.TOOL_ERROR.value
            )
            self.run_store.update_run(
                run_id,
                status=RunStatus.FAILED.value,
                termination_reason=reason,
                finished_at=utc_now(),
                error={"type": type(exc).__name__, "message": message},
            )
            self.run_store.append_event(
                run_id,
                event_type="run_failed",
                phase="FAILED",
                status="failed",
                summary={"termination_reason": reason, "error_type": type(exc).__name__},
            )
            return None

    def _execute_orchestrated(
        self,
        run_id: str,
        goal: SceneGoalSpec,
        plan: ObservationPlan,
        orchestrator_factory: Callable[
            [Callable[[], bool], Callable[[str, Mapping[str, Any]], None]],
            Any,
        ],
    ) -> dict[str, Any] | None:
        try:
            if self.run_store.is_cancel_requested(run_id):
                self._finish_cancelled(run_id)
                return None
            run = self.run_store.update_run(
                run_id,
                status=RunStatus.RUNNING.value,
                started_at=utc_now(),
            )
            self.run_store.append_event(
                run_id,
                event_type="orchestration_started",
                phase="PREPARE",
                status="success",
                summary={"goal_id": goal.goal_id},
            )

            def record_event(event_type: str, summary: Mapping[str, Any]) -> None:
                self.run_store.append_event(
                    run_id,
                    event_type=event_type,
                    phase="ORCHESTRATE",
                    status=str(summary.get("status") or "running"),
                    summary=summary,
                )

            orchestrator = orchestrator_factory(
                lambda: self.run_store.is_cancel_requested(run_id),
                record_event,
            )
            result = orchestrator.run(goal, plan=plan, budgets=run.budgets)
            document = result.as_dict()
            artifact_refs: list[str] = []

            def save(artifact_id: str, value: Mapping[str, Any]) -> None:
                artifact = self.run_store.write_json_artifact(run_id, artifact_id, value)
                artifact_refs.append(artifact["relative_path"])

            save("goal_spec.json", goal.as_dict())
            save("observation_plan.json", plan.as_dict())
            for item in document["snapshots"]:
                save(f"{item['snapshot_id']}.json", item)
            for item in document["validation_reports"]:
                save(f"{item['report_id']}.json", item)
            for item in document["scene_diffs"]:
                save(f"{item['diff_id']}.json", item)
            for item in document["repair_scopes"]:
                save(f"{item['repair_id']}.json", item)
            save("orchestration_result.json", document)

            if result.status == "cancelled":
                self._finish_cancelled(run_id)
                return None
            final_status = (
                RunStatus.SUCCEEDED.value
                if result.status == "succeeded"
                else RunStatus.FAILED.value
            )
            self.run_store.update_run(
                run_id,
                status=final_status,
                termination_reason=result.termination_reason,
                finished_at=utc_now(),
                final_answer=(
                    "场景约束验证通过"
                    if result.status == "succeeded"
                    else "场景任务未通过确定性验证"
                ),
                error=result.error,
                metrics=result.metrics,
            )
            self.run_store.append_event(
                run_id,
                event_type="orchestration_completed",
                phase="FINISH",
                status=result.status,
                summary={"termination_reason": result.termination_reason},
                artifact_refs=artifact_refs,
            )
            return document
        except Exception as exc:
            self.run_store.update_run(
                run_id,
                status=RunStatus.FAILED.value,
                termination_reason=TerminationReason.TOOL_ERROR.value,
                finished_at=utc_now(),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            self.run_store.append_event(
                run_id,
                event_type="orchestration_failed",
                phase="FAILED",
                status="failed",
                summary={"error_type": type(exc).__name__},
            )
            return None

    def cancel(self, run_id: str) -> TaskRun:
        try:
            run = self.run_store.request_cancel(run_id)
        except RunStoreError as exc:
            raise RunManagerError(str(exc)) from exc
        if RunStatus(run.status).terminal:
            return run
        with self._lock:
            future = self._futures.get(run_id)
        if future is not None and future.cancel():
            self._finish_cancelled(run_id)
        return self.run_store.get_run(run_id)

    def wait(self, run_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            future = self._futures.get(run_id)
        if future is not None:
            future.result(timeout=timeout)
        run = self.run_store.get_run(run_id)
        if run.status == RunStatus.CANCELLED.value:
            raise RunManagerError("运行已取消")
        if run.status == RunStatus.FAILED.value:
            message = str((run.error or {}).get("message") or "运行失败")
            raise RunManagerError(message)
        try:
            return self.run_store.read_json_artifact(run_id, "agent_result.json")
        except RunStoreError as exc:
            try:
                return self.run_store.read_json_artifact(
                    run_id, "orchestration_result.json"
                )
            except RunStoreError:
                raise RunManagerError(f"运行尚未产生结果：{run_id}") from exc

    def shutdown(self, *, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=False)
