"""工作区级 SceneGoalSpec 草案和冻结版本存储。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from goal_models import ObservationPlan, SceneGoalSpec, goal_content_hash, utc_now
from knowledge_base import DEFAULT_DATA_ROOT


class GoalStoreError(RuntimeError):
    """目标读取、保存或审批冲突。"""


class GoalStore:
    def __init__(
        self,
        workspace_id: str,
        *,
        data_root: Path | str = DEFAULT_DATA_ROOT,
    ) -> None:
        if not workspace_id.strip():
            raise GoalStoreError("workspace_id 不能为空")
        self.workspace_id = workspace_id
        self.root = Path(data_root) / workspace_id / "goals"
        self.plans_root = self.root / "observation_plans"
        self.root.mkdir(parents=True, exist_ok=True)
        self.plans_root.mkdir(parents=True, exist_ok=True)

    def _path(self, goal_id: str) -> Path:
        if not goal_id.startswith("goal_") or Path(goal_id).name != goal_id:
            raise GoalStoreError("goal_id 格式无效")
        return self.root / f"{goal_id}.json"

    @staticmethod
    def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=".goal-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def save_draft(self, goal: SceneGoalSpec) -> SceneGoalSpec:
        if goal.approved:
            raise GoalStoreError("已批准目标不能作为草案保存")
        path = self._path(goal.goal_id)
        if path.exists() and self.get(goal.goal_id).approved:
            raise GoalStoreError("目标已经冻结，不能覆盖")
        document = goal.as_dict()
        draft_hash = goal_content_hash(document)
        document["approval"] = {
            "status": "draft",
            "draft_hash": draft_hash,
            "approved_at": None,
            "approved_by": None,
            "frozen_hash": None,
        }
        validated = SceneGoalSpec.from_dict(document)
        self._atomic_write(path, validated.as_dict())
        return validated

    def get(self, goal_id: str) -> SceneGoalSpec:
        path = self._path(goal_id)
        if not path.exists():
            raise GoalStoreError(f"未找到目标：{goal_id}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GoalStoreError(f"目标文件损坏：{goal_id}") from exc
        return SceneGoalSpec.from_dict(document)

    def list(self) -> list[SceneGoalSpec]:
        goals: list[SceneGoalSpec] = []
        for path in sorted(self.root.glob("goal_*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                goals.append(SceneGoalSpec.from_dict(document))
            except (OSError, json.JSONDecodeError) as exc:
                raise GoalStoreError(f"目标文件损坏：{path.name}") from exc
        return goals

    def approve(
        self,
        goal_id: str,
        *,
        approved_by: str,
        expected_draft_hash: str,
    ) -> SceneGoalSpec:
        goal = self.get(goal_id)
        if goal.approved:
            return goal
        actual_hash = goal_content_hash(goal.as_dict())
        if not expected_draft_hash or expected_draft_hash != actual_hash:
            raise GoalStoreError("目标草案已变化，请重新审核后再批准")
        document = goal.as_dict()
        document["approval"] = {
            "status": "approved",
            "draft_hash": actual_hash,
            "approved_at": utc_now(),
            "approved_by": approved_by.strip() or "user",
            "frozen_hash": actual_hash,
        }
        approved = SceneGoalSpec.from_dict(document)
        self._atomic_write(self._path(goal_id), approved.as_dict())
        return approved

    def save_observation_plan(self, plan: ObservationPlan) -> Path:
        # dataclass 内部包含可变字典；落盘前重新校验内容派生 ID 和完整性字段。
        plan = ObservationPlan.from_dict(plan.as_dict())
        goal = self.get(plan.goal_id)
        if not goal.approved:
            raise GoalStoreError("草案目标不能保存执行观测计划")
        if plan.goal_hash != goal_content_hash(goal.as_dict()):
            raise GoalStoreError("观测计划对应的目标哈希已经失效")
        path = self.plans_root / f"{plan.plan_id}.json"
        self._atomic_write(path, plan.as_dict())
        return path
