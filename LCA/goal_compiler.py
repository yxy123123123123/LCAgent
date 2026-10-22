"""将用户需求编译成可审核的 SceneGoalSpec 草案。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from goal_models import SceneGoalSpec, goal_content_hash, new_goal_id
from model_gateway import ModelGateway


class GoalCompilerError(RuntimeError):
    """模型目标草案无法解析或规范化。"""


MODEL_ALLOWED_FIELDS = {
    "goal_id",
    "version",
    "task_text",
    "entities",
    "required_constraints",
    "optional_constraints",
    "protected_entities",
    "allowed_resources",
    "tolerances",
    "domain_extensions",
    "approval",
}


DEFAULT_OBSERVATIONS: dict[str, tuple[str, ...]] = {
    "entity_exists": ("identity",),
    "entity_count": ("identity",),
    "entity_type": ("identity", "class"),
    "property_equals": ("properties",),
    "property_contains": ("properties",),
    "property_range": ("properties",),
    "resource_reference_valid": ("identity", "asset_reference"),
    "geometric_relation": ("transform", "bounds"),
    "protected_unchanged": ("identity", "transform", "bounds", "asset_reference", "tags"),
}


GOAL_DRAFT_SYSTEM_PROMPT = """你是 LCAgent 的目标编译器。把用户需求转换为唯一 JSON 对象，不要输出解释或 Markdown。
模型只能生成草案，不能把 approval.status 设置为 approved。
entities 中每个对象必须包含 entity_id、label 和 selector；selector 至少包含 actor_name、actor_guid、class_name、asset_reference、level_id、tags 中的一项。
每个约束必须包含 constraint_id、type、subjects、parameters。支持的 type：entity_exists、entity_count、entity_type、property_equals、property_contains、property_range、resource_reference_valid、geometric_relation、protected_unchanged。
首个 Actor 属性闭环使用 property_range，属性路径写成 transform.location.x，parameters 至少包含 property，以及 minimum 或 maximum。
顶层字段只允许 entities、required_constraints、optional_constraints、protected_entities、allowed_resources、tolerances、domain_extensions；不要生成 goal_id、task_text 或 approval。
"""


def _strip_json_fence(text: str) -> str:
    clean = text.strip()
    if not clean.startswith("```"):
        return clean
    lines = clean.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        raise GoalCompilerError("目标草案的 JSON 代码围栏不完整")
    if lines[0].strip().casefold() not in {"```", "```json"}:
        raise GoalCompilerError("目标草案只允许 JSON 代码围栏")
    return "\n".join(lines[1:-1]).strip()


def _derive_observations(constraint: Mapping[str, Any]) -> list[str]:
    supplied = constraint.get("required_observations")
    if isinstance(supplied, list) and supplied:
        return list(dict.fromkeys(str(item).strip() for item in supplied if str(item).strip()))
    constraint_type = str(constraint.get("type") or "")
    parameters = constraint.get("parameters")
    if constraint_type.startswith("property_") and isinstance(parameters, Mapping):
        property_path = str(parameters.get("property") or "").strip()
        if property_path:
            return [property_path]
    return list(DEFAULT_OBSERVATIONS.get(constraint_type, ()))


@dataclass(frozen=True)
class GoalCompilationResult:
    goal: SceneGoalSpec
    model: str | None = None
    profile_id: str | None = None
    usage: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal.as_dict(),
            "draft_hash": goal_content_hash(self.goal.as_dict()),
            "model": self.model,
            "profile_id": self.profile_id,
            "usage": dict(self.usage or {}),
        }


class GoalCompiler:
    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.model_gateway = model_gateway or ModelGateway()

    @staticmethod
    def _parse_model_document(text: str) -> dict[str, Any]:
        try:
            value = json.loads(_strip_json_fence(text))
        except json.JSONDecodeError as exc:
            raise GoalCompilerError("模型必须输出唯一且完整的目标 JSON 对象") from exc
        if not isinstance(value, dict):
            raise GoalCompilerError("目标草案顶层必须是 JSON 对象")
        unknown = sorted(set(value) - MODEL_ALLOWED_FIELDS)
        if unknown:
            raise GoalCompilerError(f"目标草案包含未知字段：{', '.join(unknown)}")
        return value

    @staticmethod
    def compile_document(
        task_text: str,
        document: Mapping[str, Any],
        *,
        goal_id: str | None = None,
    ) -> SceneGoalSpec:
        """规范化模型或用户编辑的文档，并强制降为 draft。"""

        clean_task = task_text.strip()
        if not clean_task:
            raise GoalCompilerError("task_text 不能为空")
        unknown = sorted(set(document) - MODEL_ALLOWED_FIELDS)
        if unknown:
            raise GoalCompilerError(f"目标草案包含未知字段：{', '.join(unknown)}")
        normalized = json.loads(json.dumps(dict(document), ensure_ascii=False))
        normalized["goal_id"] = goal_id or str(normalized.get("goal_id") or new_goal_id())
        normalized["version"] = "1.0"
        normalized["task_text"] = clean_task
        normalized.setdefault("entities", [])
        normalized.setdefault("required_constraints", [])
        normalized.setdefault("optional_constraints", [])
        normalized.setdefault("protected_entities", [])
        normalized.setdefault("allowed_resources", [])
        normalized.setdefault("tolerances", {})
        normalized.setdefault("domain_extensions", {})
        for key in ("required_constraints", "optional_constraints"):
            if not isinstance(normalized[key], list):
                continue
            for index, constraint in enumerate(normalized[key]):
                if not isinstance(constraint, Mapping):
                    continue
                clean_constraint = dict(constraint)
                clean_constraint["required_observations"] = _derive_observations(
                    clean_constraint
                )
                normalized[key][index] = clean_constraint
        # 模型无权批准自己的目标；哈希由 GoalStore 保存时写入。
        normalized["approval"] = {
            "status": "draft",
            "draft_hash": None,
            "approved_at": None,
            "approved_by": None,
            "frozen_hash": None,
        }
        return SceneGoalSpec.from_dict(normalized)

    def compile(self, task_text: str, *, profile_id: str | None = None) -> GoalCompilationResult:
        clean_task = task_text.strip()
        if not clean_task:
            raise GoalCompilerError("task_text 不能为空")
        reply = self.model_gateway.chat(
            [
                {"role": "system", "content": GOAL_DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": clean_task},
            ],
            profile_id=profile_id,
        )
        document = self._parse_model_document(reply.content)
        goal = self.compile_document(clean_task, document)
        return GoalCompilationResult(
            goal=goal,
            model=reply.model,
            profile_id=reply.profile_id,
            usage=reply.usage,
        )
