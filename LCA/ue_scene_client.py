"""UnrealMCP 通用场景查询客户端与分页一致性检查。"""

from __future__ import annotations

from typing import Any, Mapping

from scene_models import SceneSnapshot, SceneSnapshotBuilder
from ue_protocol import UECapabilities, UEProtocolClient, UEProtocolError


class UESceneQueryError(UEProtocolError):
    """UE 场景分页响应不完整或页间状态不一致。"""


def validate_scene_query_capability(
    capabilities: UECapabilities,
    query: Mapping[str, Any],
) -> None:
    """执行前检查插件是否明确声明查询字段、过滤器和分页能力。"""

    if "query_scene_state" not in capabilities.capabilities:
        raise UESceneQueryError("UE 插件未声明 query_scene_state 能力")
    fields = {str(item) for item in query.get("fields") or []}
    filters = {str(item) for item in dict(query.get("filter") or {})}
    missing_fields = sorted(fields - set(capabilities.scene_query_supported_fields))
    missing_filters = sorted(filters - set(capabilities.scene_query_supported_filters))
    if missing_fields:
        raise UESceneQueryError(f"UE 不支持场景字段：{', '.join(missing_fields)}")
    if missing_filters:
        raise UESceneQueryError(f"UE 不支持场景过滤器：{', '.join(missing_filters)}")
    if not capabilities.scene_query_supports_pagination:
        raise UESceneQueryError("UE 场景查询未声明可靠分页能力")
    page_size = int(query.get("page_size") or 0)
    if (
        capabilities.scene_query_max_page_size is not None
        and page_size > capabilities.scene_query_max_page_size
    ):
        raise UESceneQueryError(
            f"请求 page_size={page_size} 超过 UE 上限 "
            f"{capabilities.scene_query_max_page_size}"
        )


def query_scene_snapshot(
    client: UEProtocolClient,
    query: Mapping[str, Any],
    *,
    capabilities: UECapabilities | None = None,
    max_pages: int = 10000,
) -> SceneSnapshot:
    """读取所有分页并形成快照；页间场景发生变化时拒绝拼接。"""

    query_document = dict(query)
    if capabilities is not None:
        validate_scene_query_capability(capabilities, query_document)
    if max_pages < 1:
        raise UESceneQueryError("max_pages 必须是正整数")

    pages: list[dict[str, Any]] = []
    cursor: Any = None
    seen_cursors: set[str] = set()
    snapshot_token: str | None = None
    expected_query_id = str(query_document.get("query_id") or "")
    for _ in range(max_pages):
        params = dict(query_document)
        params["cursor"] = cursor
        page = client.request("query_scene_state", params)
        if expected_query_id and str(page.get("query_id") or "") != expected_query_id:
            raise UESceneQueryError("UE 场景响应 query_id 与请求不一致")
        current_token = str(page.get("snapshot_token") or "").strip()
        if not current_token:
            raise UESceneQueryError("UE 场景响应缺少 snapshot_token")
        if snapshot_token is None:
            snapshot_token = current_token
        elif current_token != snapshot_token:
            raise UESceneQueryError("翻页期间 UE 场景发生变化，请重新查询")
        pages.append(page)

        next_cursor = page.get("next_cursor")
        has_more = bool(page.get("has_more", next_cursor is not None))
        if not has_more:
            if next_cursor is not None:
                raise UESceneQueryError("UE 返回 has_more=false 但仍提供 next_cursor")
            break
        if next_cursor is None:
            raise UESceneQueryError("UE 返回 has_more=true 但缺少 next_cursor")
        cursor_key = str(next_cursor)
        if cursor_key in seen_cursors:
            raise UESceneQueryError("UE 场景分页 cursor 出现循环")
        seen_cursors.add(cursor_key)
        cursor = next_cursor
    else:
        raise UESceneQueryError(f"UE 场景分页超过安全上限 {max_pages}")

    return SceneSnapshotBuilder().build(query_document, pages)
