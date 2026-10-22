"""依赖驱动的修复范围推导与写集合保护。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from constraint_engine import ValidationReport, _selector_matches
from goal_models import SceneGoalSpec, canonical_hash, goal_content_hash
from scene_diff import SceneDiff
from scene_models import SceneSnapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPAIR_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "repair" / "repair_scope.schema.json"
)


class RepairScopeError(RuntimeError):
    """约束不可自动修复，或声明/真实写集合越权。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _property_dependencies(path: str) -> set[str]:
    result = {path}
    if path.startswith("transform.location."):
        axis = path.rsplit(".", 1)[-1]
        result.add(f"bounds.origin.{axis}")
    elif path == "transform" or path.startswith("transform.rotation") or path.startswith("transform.scale"):
        result.add("bounds")
    return result


def _repair_rule(constraint: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    constraint_type = str(constraint["type"])
    parameters = constraint.get("parameters") or {}
    if constraint_type in {"property_range", "property_equals", "property_contains"}:
        path = str(parameters.get("property") or "").strip()
        if not path:
            raise RepairScopeError("REPAIR_RULE_MISSING", "属性约束缺少 property")
        return {"update"}, {path}
    if constraint_type == "entity_exists":
        return {"create"}, {"__entity__"}
    if constraint_type == "resource_reference_valid":
        return {"update"}, {"asset_reference"}
    if constraint_type == "geometric_relation":
        return {"update"}, {"transform"}
    raise RepairScopeError(
        "CONSTRAINT_NOT_AUTOREPAIRABLE",
        f"约束类型 {constraint_type} 没有安全的自动修复规则",
    )


@dataclass(frozen=True)
class RepairScope:
    repair_id: str
    goal_id: str
    goal_hash: str
    failed_constraints: tuple[str, ...]
    allowed_entities: tuple[str, ...]
    allowed_actual_entity_ids: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    allowed_properties: tuple[str, ...]
    allowed_observed_paths: tuple[str, ...]
    protected_entities: tuple[str, ...]
    protected_actual_entity_ids: tuple[str, ...]
    required_revalidation: tuple[str, ...]
    remaining_budget: dict[str, int]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairScope":
        document = _json_copy(dict(value))
        schema = json.loads(DEFAULT_REPAIR_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            raise RepairScopeError(
                "INVALID_REPAIR_SCOPE",
                f"RepairScope 不满足 Schema：{errors[0].message}",
            )
        body = dict(document)
        body.pop("repair_id")
        if document["repair_id"] != f"repair_{canonical_hash(body)[:16]}":
            raise RepairScopeError("REPAIR_SCOPE_HASH_MISMATCH", "修复范围内容哈希不一致")
        return cls(
            repair_id=document["repair_id"],
            goal_id=document["goal_id"],
            goal_hash=document["goal_hash"],
            failed_constraints=tuple(document["failed_constraints"]),
            allowed_entities=tuple(document["allowed_entities"]),
            allowed_actual_entity_ids=tuple(document["allowed_actual_entity_ids"]),
            allowed_operations=tuple(document["allowed_operations"]),
            allowed_properties=tuple(document["allowed_properties"]),
            allowed_observed_paths=tuple(document["allowed_observed_paths"]),
            protected_entities=tuple(document["protected_entities"]),
            protected_actual_entity_ids=tuple(document["protected_actual_entity_ids"]),
            required_revalidation=tuple(document["required_revalidation"]),
            remaining_budget=dict(document["remaining_budget"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "repair_id": self.repair_id,
            "goal_id": self.goal_id,
            "goal_hash": self.goal_hash,
            "failed_constraints": list(self.failed_constraints),
            "allowed_entities": list(self.allowed_entities),
            "allowed_actual_entity_ids": list(self.allowed_actual_entity_ids),
            "allowed_operations": list(self.allowed_operations),
            "allowed_properties": list(self.allowed_properties),
            "allowed_observed_paths": list(self.allowed_observed_paths),
            "protected_entities": list(self.protected_entities),
            "protected_actual_entity_ids": list(self.protected_actual_entity_ids),
            "required_revalidation": list(self.required_revalidation),
            "remaining_budget": dict(self.remaining_budget),
        }


class RepairScopeController:
    @staticmethod
    def _actual_ids(
        goal: SceneGoalSpec,
        snapshot: SceneSnapshot,
        logical_ids: set[str],
    ) -> set[str]:
        specs = {item["entity_id"]: item for item in goal.entities}
        return {
            str(entity["entity_id"])
            for logical_id in logical_ids
            for entity in snapshot.entities
            if _selector_matches(specs[logical_id]["selector"], entity)
        }

    def _build(
        self,
        goal: SceneGoalSpec,
        snapshot: SceneSnapshot,
        constraints: Sequence[Mapping[str, Any]],
        *,
        failed_constraints: Sequence[str],
        remaining_goal_repairs: int,
        remaining_tool_actions: int,
    ) -> RepairScope:
        allowed_entities: set[str] = set()
        allowed_operations: set[str] = set()
        allowed_properties: set[str] = set()
        for constraint in constraints:
            operations, properties = _repair_rule(constraint)
            subjects = set(constraint["subjects"])
            if subjects & set(goal.protected_entities):
                raise RepairScopeError(
                    "PROTECTED_ENTITY_REPAIR",
                    "失败约束要求修改受保护实体，禁止自动修复",
                )
            allowed_entities.update(subjects)
            allowed_operations.update(operations)
            allowed_properties.update(properties)

        observed_paths = {
            dependency
            for path in allowed_properties
            for dependency in _property_dependencies(path)
        }
        affected = set(allowed_entities)
        required_revalidation = {
            str(constraint["constraint_id"])
            for constraint in list(goal.required_constraints) + list(goal.optional_constraints)
            if affected & set(constraint["subjects"])
        }
        required_revalidation.update(
            f"protected_unchanged_{entity_id}" for entity_id in goal.protected_entities
        )
        protected = set(goal.protected_entities)
        body: dict[str, Any] = {
            "goal_id": goal.goal_id,
            "goal_hash": goal_content_hash(goal.as_dict()),
            "failed_constraints": sorted(set(failed_constraints)),
            "allowed_entities": sorted(allowed_entities),
            "allowed_actual_entity_ids": sorted(
                self._actual_ids(goal, snapshot, allowed_entities)
            ),
            "allowed_operations": sorted(allowed_operations),
            "allowed_properties": sorted(allowed_properties),
            "allowed_observed_paths": sorted(observed_paths),
            "protected_entities": sorted(protected),
            "protected_actual_entity_ids": sorted(
                self._actual_ids(goal, snapshot, protected)
            ),
            "required_revalidation": sorted(required_revalidation),
            "remaining_budget": {
                "goal_repairs": max(0, int(remaining_goal_repairs)),
                "tool_actions": max(0, int(remaining_tool_actions)),
            },
        }
        body["repair_id"] = f"repair_{canonical_hash(body)[:16]}"
        return RepairScope.from_dict(body)

    def derive_initial(
        self,
        goal: SceneGoalSpec,
        snapshot: SceneSnapshot,
        *,
        remaining_tool_actions: int,
    ) -> RepairScope:
        repairable: list[Mapping[str, Any]] = []
        for constraint in goal.required_constraints:
            try:
                _repair_rule(constraint)
            except RepairScopeError:
                continue
            repairable.append(constraint)
        if not repairable:
            raise RepairScopeError("NO_WRITABLE_GOAL", "目标没有可安全写入的约束")
        return self._build(
            goal,
            snapshot,
            repairable,
            failed_constraints=[],
            remaining_goal_repairs=0,
            remaining_tool_actions=remaining_tool_actions,
        )

    def derive(
        self,
        goal: SceneGoalSpec,
        report: ValidationReport,
        snapshot: SceneSnapshot,
        *,
        remaining_goal_repairs: int,
        remaining_tool_actions: int,
    ) -> RepairScope:
        if report.goal_id != goal.goal_id or report.snapshot_id != snapshot.snapshot_id:
            raise RepairScopeError("REPAIR_INPUT_MISMATCH", "验证报告与目标或快照不匹配")
        failed_ids = {
            item["constraint_id"]
            for item in report.results
            if item["required"] and item["status"] == "failed"
        }
        if not failed_ids:
            raise RepairScopeError("NO_REPAIRABLE_FAILURE", "没有可修复的失败约束")
        by_id = {
            str(item["constraint_id"]): item
            for item in list(goal.required_constraints) + list(goal.optional_constraints)
        }
        constraints: list[Mapping[str, Any]] = []
        for constraint_id in sorted(failed_ids):
            constraint = by_id.get(constraint_id)
            if constraint is None:
                raise RepairScopeError(
                    "CONSTRAINT_NOT_AUTOREPAIRABLE",
                    f"失败约束 {constraint_id} 不是可自动修复的目标约束",
                )
            constraints.append(constraint)
        return self._build(
            goal,
            snapshot,
            constraints,
            failed_constraints=sorted(failed_ids),
            remaining_goal_repairs=remaining_goal_repairs,
            remaining_tool_actions=remaining_tool_actions,
        )

    @staticmethod
    def validate_declared_writes(
        scope: RepairScope,
        writes: Sequence[Mapping[str, Any]],
    ) -> None:
        scope = RepairScope.from_dict(scope.as_dict())
        if not writes:
            raise RepairScopeError("WRITE_SET_MISSING", "动作必须声明写集合")
        allowed_entities = set(scope.allowed_entities)
        allowed_operations = set(scope.allowed_operations)
        allowed_properties = set(scope.allowed_properties)
        protected = set(scope.protected_entities)
        for item in writes:
            entity_id = str(item.get("entity_id") or "")
            operation = str(item.get("operation") or "")
            properties = {str(value) for value in item.get("properties") or []}
            if entity_id in protected:
                raise RepairScopeError("PROTECTED_ENTITY_WRITE", f"禁止修改保护对象：{entity_id}")
            if entity_id not in allowed_entities:
                raise RepairScopeError("ENTITY_WRITE_OUT_OF_SCOPE", f"实体写入越界：{entity_id}")
            if operation not in allowed_operations:
                raise RepairScopeError("OPERATION_OUT_OF_SCOPE", f"写操作越界：{operation}")
            if not properties or not properties.issubset(allowed_properties):
                raise RepairScopeError(
                    "PROPERTY_WRITE_OUT_OF_SCOPE",
                    f"属性写入越界：{', '.join(sorted(properties - allowed_properties))}",
                )

    @staticmethod
    def validate_observed_diff(scope: RepairScope, scene_diff: SceneDiff) -> None:
        scope = RepairScope.from_dict(scope.as_dict())
        scene_diff = SceneDiff.from_dict(scene_diff.as_dict())
        if scene_diff.status != "comparable":
            raise RepairScopeError(
                "OBSERVED_DIFF_INCONCLUSIVE",
                "执行后的场景差异不可比较，不能证明写入未越界",
            )
        protected = set(scope.protected_actual_entity_ids)
        changed_ids = (
            set(scene_diff.added_entities)
            | set(scene_diff.removed_entities)
            | {item["entity_id"] for item in scene_diff.modified_entities}
        )
        if changed_ids & protected:
            raise RepairScopeError("PROTECTED_ENTITY_CHANGED", "真实差异包含保护对象变化")
        allowed_actual = set(scope.allowed_actual_entity_ids)
        unexpected_entities = changed_ids - allowed_actual
        if unexpected_entities:
            raise RepairScopeError(
                "OBSERVED_ENTITY_OUT_OF_SCOPE",
                f"真实差异包含越界实体：{', '.join(sorted(unexpected_entities))}",
            )
        allowed_paths = set(scope.allowed_observed_paths)
        for entity in scene_diff.modified_entities:
            unexpected_paths = {
                change["path"]
                for change in entity["changes"]
                if not any(
                    change["path"] == allowed
                    or change["path"].startswith(f"{allowed}.")
                    or allowed.startswith(f"{change['path']}.")
                    for allowed in allowed_paths
                )
            }
            if unexpected_paths:
                raise RepairScopeError(
                    "OBSERVED_PROPERTY_OUT_OF_SCOPE",
                    f"真实差异包含越界属性：{', '.join(sorted(unexpected_paths))}",
                )
