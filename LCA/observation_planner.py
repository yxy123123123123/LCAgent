"""从冻结约束确定性编译任务相关的 UE 观测计划。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Mapping

from goal_models import ObservationPlan, SceneGoalSpec, canonical_hash, goal_content_hash


class ObservationPlanError(RuntimeError):
    """目标不可编译或 UE 无法提供必要观测。"""

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


def _field_root(path: str) -> str:
    return path.split(".", 1)[0]


class ObservationPlanner:
    DEFAULT_PAGE_SIZE = 200

    @staticmethod
    def _selector_filter(entity: Mapping[str, Any]) -> dict[str, Any]:
        selector = dict(entity["selector"])
        # 保留显式选择器，不把逻辑 entity_id 误当成 UE Actor GUID。
        return {key: selector[key] for key in sorted(selector)}

    def compile(
        self,
        goal: SceneGoalSpec,
        *,
        require_approved: bool = True,
    ) -> ObservationPlan:
        # 防止调用方在 dataclass 创建后修改内部字典并绕过冻结哈希。
        goal = SceneGoalSpec.from_dict(goal.as_dict())
        if require_approved and not goal.approved:
            raise ObservationPlanError("GOAL_NOT_APPROVED", "目标尚未审核冻结")

        entities = {item["entity_id"]: item for item in goal.entities}
        grouped_fields: dict[str, set[str]] = defaultdict(set)
        grouped_filters: dict[str, dict[str, Any]] = {}
        evidence: list[dict[str, Any]] = []

        constraints = list(goal.required_constraints) + list(goal.optional_constraints)
        for constraint in sorted(constraints, key=lambda item: item["constraint_id"]):
            required_paths = sorted(set(constraint["required_observations"]))
            if not required_paths:
                raise ObservationPlanError(
                    "OBSERVATION_REQUIREMENT_MISSING",
                    f"约束 {constraint['constraint_id']} 没有声明必要观测",
                )
            for entity_id in sorted(constraint["subjects"]):
                entity = entities[entity_id]
                query_filter = self._selector_filter(entity)
                key = json.dumps(query_filter, ensure_ascii=False, sort_keys=True)
                grouped_filters[key] = query_filter
                grouped_fields[key].update({"identity", *(_field_root(path) for path in required_paths)})
                evidence.append(
                    {
                        "constraint_id": constraint["constraint_id"],
                        "entity_id": entity_id,
                        "required_fields": required_paths,
                    }
                )

        # protected_entities 本身就是成功条件；即使模型漏写显式约束，也必须
        # 采集足以比较前后状态的字段，不能把“未观测”误当成“未改变”。
        protected_fields = ["asset_reference", "bounds", "identity", "tags", "transform"]
        for entity_id in sorted(goal.protected_entities):
            entity = entities[entity_id]
            query_filter = self._selector_filter(entity)
            key = json.dumps(query_filter, ensure_ascii=False, sort_keys=True)
            grouped_filters[key] = query_filter
            grouped_fields[key].update(protected_fields)
            evidence.append(
                {
                    "constraint_id": f"protected_unchanged_{entity_id}",
                    "entity_id": entity_id,
                    "required_fields": protected_fields,
                }
            )

        queries: list[dict[str, Any]] = []
        for key in sorted(grouped_filters):
            query_filter = grouped_filters[key]
            fields = sorted(grouped_fields[key])
            query_hash = canonical_hash({"filter": query_filter, "fields": fields})[:12]
            queries.append(
                {
                    "query_id": f"query_{query_hash}",
                    "filter": query_filter,
                    "fields": fields,
                    "page_size": self.DEFAULT_PAGE_SIZE,
                }
            )

        evidence.sort(key=lambda item: (item["constraint_id"], item["entity_id"]))
        all_required = sorted(
            {
                field
                for item in evidence
                for field in item["required_fields"]
            }
        )
        body = {
            "version": "1.0",
            "goal_id": goal.goal_id,
            "goal_hash": goal_content_hash(goal.as_dict()),
            "queries": queries,
            "evidence_requirements": evidence,
            "required_completeness": {
                "allow_truncated": False,
                "required_fields": all_required,
            },
        }
        body["plan_id"] = f"observation_{canonical_hash(body)[:16]}"
        return ObservationPlan.from_dict(body)

    @staticmethod
    def validate_capabilities(
        plan: ObservationPlan,
        *,
        supported_fields: list[str] | tuple[str, ...] | set[str],
        supports_pagination: bool,
    ) -> None:
        """在真正查询 UE 前明确报告缺失字段，禁止用伪默认值继续。"""

        plan = ObservationPlan.from_dict(plan.as_dict())
        supported = {str(item).strip() for item in supported_fields if str(item).strip()}
        required_roots = {
            _field_root(field)
            for item in plan.evidence_requirements
            for field in item["required_fields"]
        } | {"identity"}
        missing = sorted(required_roots - supported)
        if missing:
            raise ObservationPlanError(
                "OBSERVATION_FIELD_UNSUPPORTED",
                f"UE 查询缺少必要字段：{', '.join(missing)}",
                details={"missing_fields": missing},
            )
        if not plan.required_completeness["allow_truncated"] and not supports_pagination:
            raise ObservationPlanError(
                "OBSERVATION_PAGINATION_UNSUPPORTED",
                "目标要求完整观测，但 UE 查询不支持可靠分页",
            )
