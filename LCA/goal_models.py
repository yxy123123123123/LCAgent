"""场景目标与观测计划的稳定数据契约。

模型只能生成目标草案；本模块负责 Schema 和跨字段语义校验，批准状态不能由
模型直接取得。后续执行、验证和修复阶段都只读取已经冻结的目标。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOAL_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "goal" / "scene_goal_spec.schema.json"
DEFAULT_PLAN_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "goal" / "observation_plan.schema.json"


class GoalSpecError(RuntimeError):
    """目标规范或观测计划不满足契约。"""

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

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_goal_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"goal_{stamp}_{uuid4().hex[:12]}"


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def canonical_hash(value: Mapping[str, Any]) -> str:
    """计算与键顺序无关的内容哈希。"""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def goal_content_hash(value: Mapping[str, Any]) -> str:
    """审批元数据不属于用户目标内容，避免哈希自引用。"""

    content = _json_copy(value)
    content.pop("approval", None)
    return canonical_hash(content)


def _load_validator(path: Path) -> Draft202012Validator:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoalSpecError("SCHEMA_UNAVAILABLE", f"无法读取契约：{path}") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _raise_first_schema_error(
    validator: Draft202012Validator,
    document: Mapping[str, Any],
    *,
    code: str,
    label: str,
) -> None:
    errors = sorted(
        validator.iter_errors(document),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = ".".join(str(part) for part in error.absolute_path)
    location = f"（字段 {path}）" if path else ""
    raise GoalSpecError(
        code,
        f"{label}不满足 Schema{location}：{error.message}",
        details={"path": path, "validator": error.validator},
    )


@dataclass(frozen=True)
class SceneGoalSpec:
    goal_id: str
    version: str
    task_text: str
    entities: tuple[dict[str, Any], ...]
    required_constraints: tuple[dict[str, Any], ...]
    optional_constraints: tuple[dict[str, Any], ...]
    protected_entities: tuple[str, ...]
    allowed_resources: tuple[str, ...]
    tolerances: dict[str, float]
    domain_extensions: dict[str, Any]
    approval: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneGoalSpec":
        document = _json_copy(dict(value))
        _raise_first_schema_error(
            _load_validator(DEFAULT_GOAL_SCHEMA_PATH),
            document,
            code="INVALID_GOAL_SPEC",
            label="SceneGoalSpec",
        )
        entity_ids = [str(item["entity_id"]) for item in document["entities"]]
        if len(entity_ids) != len(set(entity_ids)):
            raise GoalSpecError("DUPLICATE_ENTITY_ID", "目标中的 entity_id 不能重复")

        constraints = document["required_constraints"] + document["optional_constraints"]
        constraint_ids = [str(item["constraint_id"]) for item in constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise GoalSpecError("DUPLICATE_CONSTRAINT_ID", "目标中的 constraint_id 不能重复")

        known_entities = set(entity_ids)
        for constraint in constraints:
            unknown = sorted(set(constraint["subjects"]) - known_entities)
            if unknown:
                raise GoalSpecError(
                    "UNKNOWN_CONSTRAINT_SUBJECT",
                    f"约束 {constraint['constraint_id']} 引用了未知实体：{', '.join(unknown)}",
                    details={"constraint_id": constraint["constraint_id"], "unknown": unknown},
                )
            if constraint["type"] == "property_range":
                parameters = constraint["parameters"]
                minimum = parameters.get("minimum")
                maximum = parameters.get("maximum")
                if minimum is not None and maximum is not None and minimum > maximum:
                    raise GoalSpecError(
                        "INVALID_PROPERTY_RANGE",
                        f"约束 {constraint['constraint_id']} 的 minimum 不能大于 maximum",
                    )

        unknown_protected = sorted(set(document["protected_entities"]) - known_entities)
        if unknown_protected:
            raise GoalSpecError(
                "UNKNOWN_PROTECTED_ENTITY",
                f"保护对象不存在：{', '.join(unknown_protected)}",
            )
        actual_hash = goal_content_hash(document)
        approval = document["approval"]
        if approval.get("draft_hash") not in {None, actual_hash}:
            raise GoalSpecError("GOAL_HASH_MISMATCH", "目标内容与 draft_hash 不一致")
        if approval["status"] == "approved" and approval.get("frozen_hash") != actual_hash:
            raise GoalSpecError("GOAL_HASH_MISMATCH", "冻结目标内容已发生变化")
        return cls(
            goal_id=document["goal_id"],
            version=document["version"],
            task_text=document["task_text"],
            entities=tuple(document["entities"]),
            required_constraints=tuple(document["required_constraints"]),
            optional_constraints=tuple(document["optional_constraints"]),
            protected_entities=tuple(document["protected_entities"]),
            allowed_resources=tuple(document["allowed_resources"]),
            tolerances=dict(document["tolerances"]),
            domain_extensions=dict(document["domain_extensions"]),
            approval=dict(document["approval"]),
        )

    @property
    def approved(self) -> bool:
        return self.approval.get("status") == "approved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "version": self.version,
            "task_text": self.task_text,
            "entities": _json_copy(self.entities),
            "required_constraints": _json_copy(self.required_constraints),
            "optional_constraints": _json_copy(self.optional_constraints),
            "protected_entities": list(self.protected_entities),
            "allowed_resources": list(self.allowed_resources),
            "tolerances": dict(self.tolerances),
            "domain_extensions": _json_copy(self.domain_extensions),
            "approval": _json_copy(self.approval),
        }


@dataclass(frozen=True)
class ObservationPlan:
    plan_id: str
    version: str
    goal_id: str
    goal_hash: str
    queries: tuple[dict[str, Any], ...]
    evidence_requirements: tuple[dict[str, Any], ...]
    required_completeness: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationPlan":
        document = _json_copy(dict(value))
        _raise_first_schema_error(
            _load_validator(DEFAULT_PLAN_SCHEMA_PATH),
            document,
            code="INVALID_OBSERVATION_PLAN",
            label="ObservationPlan",
        )
        query_ids = [str(item["query_id"]) for item in document["queries"]]
        if len(query_ids) != len(set(query_ids)):
            raise GoalSpecError("DUPLICATE_QUERY_ID", "观测计划中的 query_id 不能重复")
        for query in document["queries"]:
            expected_query_id = (
                "query_"
                + canonical_hash(
                    {"filter": query["filter"], "fields": query["fields"]}
                )[:12]
            )
            if query["query_id"] != expected_query_id:
                raise GoalSpecError("QUERY_HASH_MISMATCH", "观测查询内容与 query_id 不一致")
        expected_fields = sorted(
            {
                field
                for item in document["evidence_requirements"]
                for field in item["required_fields"]
            }
        )
        if document["required_completeness"]["required_fields"] != expected_fields:
            raise GoalSpecError(
                "PLAN_COMPLETENESS_MISMATCH",
                "required_completeness 与证据字段不一致",
            )
        plan_body = dict(document)
        plan_body.pop("plan_id")
        expected_plan_id = f"observation_{canonical_hash(plan_body)[:16]}"
        if document["plan_id"] != expected_plan_id:
            raise GoalSpecError("PLAN_HASH_MISMATCH", "观测计划内容与 plan_id 不一致")
        return cls(
            plan_id=document["plan_id"],
            version=document["version"],
            goal_id=document["goal_id"],
            goal_hash=document["goal_hash"],
            queries=tuple(document["queries"]),
            evidence_requirements=tuple(document["evidence_requirements"]),
            required_completeness=dict(document["required_completeness"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "version": self.version,
            "goal_id": self.goal_id,
            "goal_hash": self.goal_hash,
            "queries": _json_copy(self.queries),
            "evidence_requirements": _json_copy(self.evidence_requirements),
            "required_completeness": _json_copy(self.required_completeness),
        }
