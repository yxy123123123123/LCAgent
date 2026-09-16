"""Deterministic multi-label router shared by G2 and G3."""

from __future__ import annotations

from typing import Any


KEYWORDS = {
    "resource": (
        "资源", "路径", "材质", "模型", "蓝图", "actor", "dem", "地形", "道路", "河流", "建筑",
    ),
    "standard": (
        "规范", "标准", "约束", "要求", "必须", "禁止", "不得", "宽度", "间距", "距离", "布局", "方位",
    ),
    "context": (
        "保持", "不变", "已有", "原有", "之前", "上一轮", "上次", "继续", "仍然", "增加", "修改", "移动", "删除",
    ),
}


def route_query(query: str, task: dict[str, Any] | None = None) -> dict[str, Any]:
    lowered = query.lower()
    routes: list[str] = []
    reasons: dict[str, list[str]] = {}
    for knowledge_type, keywords in KEYWORDS.items():
        hits = [keyword for keyword in keywords if keyword.lower() in lowered]
        if hits:
            routes.append(knowledge_type)
            reasons[knowledge_type] = hits

    if task and int(task.get("turn_id", 1)) > 1 and "context" not in routes:
        routes.append("context")
        reasons["context"] = ["turn_id>1"]

    # A construction request normally needs a compatible UE resource. This fixed
    # fallback applies equally to G2 and G3 and is recorded for auditability.
    if "resource" not in routes:
        routes.append("resource")
        reasons["resource"] = ["construction_resource_fallback"]

    return {"routes": routes, "reasons": reasons, "router_version": "rules-v1"}

