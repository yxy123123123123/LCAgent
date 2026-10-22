"""LCAgent 持久化运行模型。

这些对象只描述任务生命周期，不包含具体地形、道路或场景约束规则。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"run_{stamp}_{uuid4().hex[:12]}"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class TerminationReason(str, Enum):
    GOAL_SATISFIED = "goal_satisfied"
    DRY_RUN_COMPLETED = "dry_run_completed"
    MODEL_FINISHED_WITHOUT_VERIFICATION = "model_finished_without_verification"
    CONSTRAINT_FAILED = "constraint_failed"
    NOT_OBSERVABLE = "not_observable"
    TOOL_ERROR = "tool_error"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    CANCELLED = "cancelled"


DEFAULT_BUDGETS: dict[str, int] = {
    "max_model_steps": 12,
    "max_execution_repairs": 3,
    "max_goal_repairs": 3,
    "max_tool_actions": 30,
    "max_elapsed_seconds": 600,
}


@dataclass(frozen=True)
class TaskRun:
    run_id: str
    workspace_id: str
    task_text: str
    model_profile_id: str | None
    embedding_profile_id: str | None
    execution_mode: str
    status: str = RunStatus.QUEUED.value
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    termination_reason: str | None = None
    final_answer: str | None = None
    error: Mapping[str, Any] | None = None
    budgets: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    metrics: Mapping[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    sequence: int
    event_type: str
    phase: str
    status: str
    timestamp: str = field(default_factory=utc_now)
    summary: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["artifact_refs"] = list(self.artifact_refs)
        return result


def normalize_budgets(value: Mapping[str, Any] | None = None) -> dict[str, int]:
    result = dict(DEFAULT_BUDGETS)
    for key, raw in dict(value or {}).items():
        if key not in result:
            raise ValueError(f"未知运行预算：{key}")
        number = int(raw)
        minimum = 0 if key in {"max_execution_repairs", "max_goal_repairs"} else 1
        if number < minimum:
            raise ValueError(f"运行预算 {key} 不能小于 {minimum}")
        result[key] = number
    return result
