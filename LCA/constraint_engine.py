"""注册式确定性场景约束引擎。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from goal_models import SceneGoalSpec, canonical_hash, goal_content_hash
from scene_diff import diff_snapshots
from scene_models import SceneSnapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "validation" / "validation_report.schema.json"
)
VALID_RESULT_STATUSES = {"passed", "failed", "not_observable", "error", "skipped"}


class ConstraintEngineError(RuntimeError):
    """约束注册或验证报告错误。"""


@dataclass(frozen=True)
class EvaluationContext:
    goal: SceneGoalSpec
    snapshot: SceneSnapshot
    before_snapshot: SceneSnapshot | None
    constraint: Mapping[str, Any]
    required: bool
    resolved_entities: Mapping[str, tuple[Mapping[str, Any], ...]]


Evaluator = Callable[[EvaluationContext], Mapping[str, Any]]


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _get_path(value: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _selector_matches(selector: Mapping[str, Any], entity: Mapping[str, Any]) -> bool:
    for key, expected in selector.items():
        if key == "actor_name" and entity.get("name") != expected:
            return False
        if key == "actor_guid":
            entity_id = str(entity.get("entity_id") or "")
            if entity_id != expected and not entity_id.casefold().endswith(str(expected).casefold()):
                return False
        if key in {"class_name", "asset_reference", "level_id"} and entity.get(key) != expected:
            return False
        if key == "tags":
            if not set(expected).issubset(set(entity.get("tags") or [])):
                return False
    return True


def _resolve_entities(
    goal: SceneGoalSpec,
    snapshot: SceneSnapshot,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    return {
        spec["entity_id"]: tuple(
            entity
            for entity in snapshot.entities
            if _selector_matches(spec["selector"], entity)
        )
        for spec in goal.entities
    }


def _result(
    context: EvaluationContext,
    status: str,
    message: str,
    *,
    measured: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
    evidence_refs: Sequence[str] = (),
    involved_entities: Sequence[str] | None = None,
) -> dict[str, Any]:
    if status not in VALID_RESULT_STATUSES:
        raise ConstraintEngineError(f"未知约束状态：{status}")
    return {
        "constraint_id": str(context.constraint["constraint_id"]),
        "constraint_type": str(context.constraint["type"]),
        "required": context.required,
        "status": status,
        "measured": _json_copy(dict(measured or {})),
        "expected": _json_copy(dict(expected or context.constraint.get("parameters") or {})),
        "involved_entities": list(
            involved_entities
            if involved_entities is not None
            else context.constraint.get("subjects") or []
        ),
        "evidence_refs": list(evidence_refs),
        "message": message,
    }


def _resolved_actual_entities(context: EvaluationContext) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    entities: list[Mapping[str, Any]] = []
    for subject in context.constraint["subjects"]:
        for entity in context.resolved_entities.get(subject, ()):
            entity_id = str(entity["entity_id"])
            if entity_id not in seen:
                seen.add(entity_id)
                entities.append(entity)
    return entities


def _require_complete(context: EvaluationContext) -> dict[str, Any] | None:
    if context.snapshot.completeness["complete"]:
        return None
    return _result(
        context,
        "not_observable",
        "场景查询不完整，不能证明约束满足",
        measured={"completeness": context.snapshot.completeness},
        evidence_refs=[f"{context.snapshot.snapshot_id}#completeness"],
    )


def _single_entities_or_result(
    context: EvaluationContext,
) -> tuple[list[Mapping[str, Any]], dict[str, Any] | None]:
    actual: list[Mapping[str, Any]] = []
    for subject in context.constraint["subjects"]:
        matches = list(context.resolved_entities.get(subject, ()))
        if not matches:
            return [], _result(
                context,
                "failed",
                f"没有找到目标实体 {subject}",
                measured={"match_count": 0},
            )
        if len(matches) > 1:
            return [], _result(
                context,
                "error",
                f"目标实体 {subject} 的选择器匹配到多个 Actor",
                measured={"match_count": len(matches)},
            )
        actual.append(matches[0])
    return actual, None


def _entity_exists(context: EvaluationContext) -> Mapping[str, Any]:
    incomplete = _require_complete(context)
    if incomplete:
        return incomplete
    counts = {
        subject: len(context.resolved_entities.get(subject, ()))
        for subject in context.constraint["subjects"]
    }
    passed = all(count > 0 for count in counts.values())
    return _result(
        context,
        "passed" if passed else "failed",
        "目标实体均存在" if passed else "至少一个目标实体不存在",
        measured={"match_counts": counts},
        expected={"minimum_each": 1},
    )


def _entity_count(context: EvaluationContext) -> Mapping[str, Any]:
    incomplete = _require_complete(context)
    if incomplete:
        return incomplete
    count = len(_resolved_actual_entities(context))
    parameters = context.constraint["parameters"]
    minimum = parameters.get("minimum")
    maximum = parameters.get("maximum")
    exact = parameters.get("exact")
    passed = True
    if exact is not None:
        passed = count == int(exact)
    if minimum is not None:
        passed = passed and count >= int(minimum)
    if maximum is not None:
        passed = passed and count <= int(maximum)
    return _result(
        context,
        "passed" if passed else "failed",
        "实体数量满足要求" if passed else "实体数量不满足要求",
        measured={"count": count},
    )


def _entity_type(context: EvaluationContext) -> Mapping[str, Any]:
    incomplete = _require_complete(context)
    if incomplete:
        return incomplete
    entities, failure = _single_entities_or_result(context)
    if failure:
        return failure
    expected_type = str(context.constraint["parameters"].get("class_name") or "")
    if not expected_type:
        raise ConstraintEngineError("entity_type 缺少 parameters.class_name")
    values = {str(entity["entity_id"]): entity.get("class_name") for entity in entities}
    if any(value is None for value in values.values()):
        return _result(
            context,
            "not_observable",
            "快照缺少 class_name",
            measured={"class_names": values},
        )
    passed = all(value == expected_type for value in values.values())
    return _result(
        context,
        "passed" if passed else "failed",
        "实体类型满足要求" if passed else "实体类型不满足要求",
        measured={"class_names": values},
        expected={"class_name": expected_type},
    )


def _property_values(
    context: EvaluationContext,
) -> tuple[str, list[Mapping[str, Any]], dict[str, Any] | None, dict[str, Any]]:
    incomplete = _require_complete(context)
    if incomplete:
        return "", [], incomplete, {}
    entities, failure = _single_entities_or_result(context)
    if failure:
        return "", [], failure, {}
    path = str(context.constraint["parameters"].get("property") or "").strip()
    if not path:
        raise ConstraintEngineError("属性约束缺少 parameters.property")
    values: dict[str, Any] = {}
    missing: list[str] = []
    for entity in entities:
        exists, value = _get_path(entity, path)
        if not exists or path not in set(entity.get("available_fields") or []):
            missing.append(str(entity["entity_id"]))
        else:
            values[str(entity["entity_id"])] = value
    if missing:
        return path, entities, _result(
            context,
            "not_observable",
            f"快照缺少必要属性 {path}",
            measured={"missing_entities": missing},
            evidence_refs=[f"{context.snapshot.snapshot_id}#completeness"],
        ), values
    return path, entities, None, values


def _property_range(context: EvaluationContext) -> Mapping[str, Any]:
    path, _entities, failure, values = _property_values(context)
    if failure:
        return failure
    parameters = context.constraint["parameters"]
    minimum = parameters.get("minimum")
    maximum = parameters.get("maximum")
    tolerance = float(
        parameters.get("tolerance", context.goal.tolerances.get(path, 0.0))
    )
    passed = True
    for value in values.values():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConstraintEngineError(f"属性 {path} 不是数值")
        if minimum is not None and float(value) < float(minimum) - tolerance:
            passed = False
        if maximum is not None and float(value) > float(maximum) + tolerance:
            passed = False
    refs = [f"{context.snapshot.snapshot_id}#{entity_id}.{path}" for entity_id in values]
    return _result(
        context,
        "passed" if passed else "failed",
        "属性值在允许范围内" if passed else "属性值超出允许范围",
        measured={"values": values, "tolerance": tolerance},
        evidence_refs=refs,
    )


def _property_equals(context: EvaluationContext) -> Mapping[str, Any]:
    path, _entities, failure, values = _property_values(context)
    if failure:
        return failure
    expected = context.constraint["parameters"]["value"]
    tolerance = float(context.constraint["parameters"].get("tolerance", 0.0))
    passed = all(
        math.isclose(float(value), float(expected), abs_tol=tolerance, rel_tol=0.0)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        else value == expected
        for value in values.values()
    )
    refs = [f"{context.snapshot.snapshot_id}#{entity_id}.{path}" for entity_id in values]
    return _result(
        context,
        "passed" if passed else "failed",
        "属性值相等" if passed else "属性值不相等",
        measured={"values": values, "tolerance": tolerance},
        evidence_refs=refs,
    )


def _property_contains(context: EvaluationContext) -> Mapping[str, Any]:
    path, _entities, failure, values = _property_values(context)
    if failure:
        return failure
    expected = context.constraint["parameters"]["value"]
    try:
        passed = all(expected in value for value in values.values())
    except TypeError as exc:
        raise ConstraintEngineError(f"属性 {path} 不支持包含判断") from exc
    refs = [f"{context.snapshot.snapshot_id}#{entity_id}.{path}" for entity_id in values]
    return _result(
        context,
        "passed" if passed else "failed",
        "属性包含目标值" if passed else "属性不包含目标值",
        measured={"values": values},
        evidence_refs=refs,
    )


def _resource_reference_valid(context: EvaluationContext) -> Mapping[str, Any]:
    incomplete = _require_complete(context)
    if incomplete:
        return incomplete
    entities, failure = _single_entities_or_result(context)
    if failure:
        return failure
    references = {
        str(entity["entity_id"]): entity.get("asset_reference") for entity in entities
    }
    if any(not value for value in references.values()):
        return _result(
            context,
            "not_observable",
            "快照缺少 asset_reference",
            measured={"references": references},
        )
    allowed = set(context.goal.allowed_resources)
    passed = not allowed or all(value in allowed for value in references.values())
    return _result(
        context,
        "passed" if passed else "failed",
        "资源引用有效" if passed else "资源引用不在允许集合中",
        measured={"references": references},
        expected={"allowed_resources": sorted(allowed)},
    )


def _aabb(entity: Mapping[str, Any]) -> tuple[list[float], list[float]] | None:
    bounds = entity.get("bounds")
    if not isinstance(bounds, Mapping):
        return None
    origin = bounds.get("origin")
    extent = bounds.get("extent")
    if not isinstance(origin, Mapping) or not isinstance(extent, Mapping):
        return None
    low = [float(origin[axis]) - float(extent[axis]) for axis in ("x", "y", "z")]
    high = [float(origin[axis]) + float(extent[axis]) for axis in ("x", "y", "z")]
    return low, high


def _geometric_relation(context: EvaluationContext) -> Mapping[str, Any]:
    incomplete = _require_complete(context)
    if incomplete:
        return incomplete
    entities, failure = _single_entities_or_result(context)
    if failure:
        return failure
    if len(entities) != 2:
        raise ConstraintEngineError("geometric_relation 当前要求恰好两个 subjects")
    relation = str(context.constraint["parameters"].get("relation") or "")
    if relation != "non_overlapping":
        raise ConstraintEngineError(f"暂不支持的几何关系：{relation}")
    boxes = [_aabb(entity) for entity in entities]
    if any(box is None for box in boxes):
        return _result(context, "not_observable", "快照缺少 bounds")
    first, second = boxes
    assert first is not None and second is not None
    overlaps = all(first[0][i] <= second[1][i] and second[0][i] <= first[1][i] for i in range(3))
    return _result(
        context,
        "failed" if overlaps else "passed",
        "实体包围盒发生重叠" if overlaps else "实体包围盒未重叠",
        measured={"overlaps": overlaps},
        expected={"relation": "non_overlapping"},
    )


def _protected_unchanged(context: EvaluationContext) -> Mapping[str, Any]:
    if context.before_snapshot is None:
        return _result(context, "not_observable", "缺少修改前快照，无法验证保护对象")
    scene_diff = diff_snapshots(
        context.before_snapshot,
        context.snapshot,
        tolerances=context.goal.tolerances,
    )
    if scene_diff.status != "comparable":
        return _result(
            context,
            "not_observable",
            "保护对象前后快照不可比较",
            measured={"diff_status": scene_diff.status, "reasons": list(scene_diff.reasons)},
            evidence_refs=[scene_diff.diff_id],
        )
    before_resolved = _resolve_entities(context.goal, context.before_snapshot)
    actual_ids: set[str] = set()
    for subject in context.constraint["subjects"]:
        before_matches = list(before_resolved.get(subject, ()))
        after_matches = list(context.resolved_entities.get(subject, ()))
        if len(before_matches) != 1:
            return _result(
                context,
                "not_observable",
                f"修改前无法唯一定位保护对象 {subject}",
                measured={"before_match_count": len(before_matches)},
                evidence_refs=[scene_diff.diff_id],
            )
        actual_ids.add(str(before_matches[0]["entity_id"]))
        if len(after_matches) != 1:
            return _result(
                context,
                "failed",
                f"修改后保护对象 {subject} 不存在或不再唯一",
                measured={"after_match_count": len(after_matches)},
                evidence_refs=[scene_diff.diff_id],
            )
        actual_ids.add(str(after_matches[0]["entity_id"]))
    modified_ids = {item["entity_id"] for item in scene_diff.modified_entities}
    changed = sorted(actual_ids & (modified_ids | set(scene_diff.removed_entities)))
    passed = not changed
    return _result(
        context,
        "passed" if passed else "failed",
        "保护对象未发生变化" if passed else "保护对象发生了超容差变化",
        measured={"changed_entities": changed},
        evidence_refs=[scene_diff.diff_id],
    )


@dataclass(frozen=True)
class ValidationReport:
    report_id: str
    goal_id: str
    goal_hash: str
    snapshot_id: str
    before_snapshot_id: str | None
    overall_status: str
    results: tuple[dict[str, Any], ...]
    summary: dict[str, int]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationReport":
        document = _json_copy(dict(value))
        schema = json.loads(DEFAULT_REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            raise ConstraintEngineError(f"ValidationReport 不满足 Schema：{errors[0].message}")
        body = dict(document)
        body.pop("report_id")
        if document["report_id"] != f"validation_{canonical_hash(body)[:16]}":
            raise ConstraintEngineError("ValidationReport 内容与 report_id 不一致")
        return cls(
            report_id=document["report_id"],
            goal_id=document["goal_id"],
            goal_hash=document["goal_hash"],
            snapshot_id=document["snapshot_id"],
            before_snapshot_id=document["before_snapshot_id"],
            overall_status=document["overall_status"],
            results=tuple(document["results"]),
            summary=dict(document["summary"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "goal_id": self.goal_id,
            "goal_hash": self.goal_hash,
            "snapshot_id": self.snapshot_id,
            "before_snapshot_id": self.before_snapshot_id,
            "overall_status": self.overall_status,
            "results": _json_copy(self.results),
            "summary": dict(self.summary),
        }


class ConstraintEngine:
    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}
        self.register("entity_exists", _entity_exists)
        self.register("entity_count", _entity_count)
        self.register("entity_type", _entity_type)
        self.register("property_equals", _property_equals)
        self.register("property_contains", _property_contains)
        self.register("property_range", _property_range)
        self.register("resource_reference_valid", _resource_reference_valid)
        self.register("geometric_relation", _geometric_relation)
        self.register("protected_unchanged", _protected_unchanged)

    def register(self, constraint_type: str, evaluator: Evaluator) -> None:
        clean_type = constraint_type.strip()
        if not clean_type:
            raise ConstraintEngineError("约束类型不能为空")
        if clean_type in self._evaluators:
            raise ConstraintEngineError(f"约束求值器重复注册：{clean_type}")
        self._evaluators[clean_type] = evaluator

    def supported_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._evaluators))

    def validate(
        self,
        goal: SceneGoalSpec,
        snapshot: SceneSnapshot,
        *,
        before_snapshot: SceneSnapshot | None = None,
    ) -> ValidationReport:
        goal = SceneGoalSpec.from_dict(goal.as_dict())
        snapshot = SceneSnapshot.from_dict(snapshot.as_dict())
        if before_snapshot is not None:
            before_snapshot = SceneSnapshot.from_dict(before_snapshot.as_dict())
        resolved = _resolve_entities(goal, snapshot)
        work: list[tuple[Mapping[str, Any], bool]] = [
            *((item, True) for item in goal.required_constraints),
            *((item, False) for item in goal.optional_constraints),
        ]
        existing_ids = {str(item["constraint_id"]) for item, _required in work}
        for entity_id in goal.protected_entities:
            constraint_id = f"protected_unchanged_{entity_id}"
            if constraint_id not in existing_ids:
                work.append(
                    (
                        {
                            "constraint_id": constraint_id,
                            "type": "protected_unchanged",
                            "subjects": [entity_id],
                            "parameters": {},
                            "required_observations": [
                                "asset_reference",
                                "bounds",
                                "identity",
                                "tags",
                                "transform",
                            ],
                        },
                        True,
                    )
                )

        results: list[dict[str, Any]] = []
        for constraint, required in work:
            evaluator = self._evaluators.get(str(constraint["type"]))
            if evaluator is None:
                result = {
                    "constraint_id": str(constraint["constraint_id"]),
                    "constraint_type": str(constraint["type"]),
                    "required": required,
                    "status": "error",
                    "measured": {},
                    "expected": _json_copy(constraint.get("parameters") or {}),
                    "involved_entities": list(constraint.get("subjects") or []),
                    "evidence_refs": [],
                    "message": "没有注册对应的确定性求值器",
                }
            else:
                context = EvaluationContext(
                    goal=goal,
                    snapshot=snapshot,
                    before_snapshot=before_snapshot,
                    constraint=constraint,
                    required=required,
                    resolved_entities=resolved,
                )
                try:
                    result = dict(evaluator(context))
                except Exception as exc:
                    result = _result(
                        context,
                        "error",
                        f"约束求值异常：{type(exc).__name__}: {exc}",
                    )
            results.append(result)

        summary = {status: 0 for status in sorted(VALID_RESULT_STATUSES)}
        for result in results:
            summary[result["status"]] += 1
        blocking = [
            item
            for item in results
            if item["required"] and item["status"] != "passed"
        ]
        if not blocking:
            overall_status = "passed"
        elif any(item["status"] == "error" for item in blocking):
            overall_status = "error"
        elif any(item["status"] == "not_observable" for item in blocking):
            overall_status = "not_observable"
        else:
            overall_status = "failed"

        body: dict[str, Any] = {
            "goal_id": goal.goal_id,
            "goal_hash": goal_content_hash(goal.as_dict()),
            "snapshot_id": snapshot.snapshot_id,
            "before_snapshot_id": before_snapshot.snapshot_id if before_snapshot else None,
            "overall_status": overall_status,
            "results": results,
            "summary": summary,
        }
        body["report_id"] = f"validation_{canonical_hash(body)[:16]}"
        return ValidationReport.from_dict(body)
