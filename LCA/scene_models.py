"""标准 SceneSnapshot 模型与分页查询结果规范化。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from goal_models import canonical_hash


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_SCHEMA_PATH = (
    PROJECT_ROOT / "contracts" / "scene" / "scene_snapshot.schema.json"
)


class SceneModelError(RuntimeError):
    """场景查询结果无法形成可信快照。"""

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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _validator() -> Draft202012Validator:
    try:
        schema = json.loads(DEFAULT_SNAPSHOT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SceneModelError("SCHEMA_UNAVAILABLE", "无法读取 SceneSnapshot Schema") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def snapshot_content(document: Mapping[str, Any]) -> dict[str, Any]:
    """排除采集时间和派生标识，得到可复现的场景内容。"""

    content = _json_copy(document)
    content.pop("snapshot_id", None)
    content.pop("content_hash", None)
    content.pop("captured_at", None)
    return content


def snapshot_content_hash(document: Mapping[str, Any]) -> str:
    return canonical_hash(snapshot_content(document))


def _available_paths(value: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if prefix:
        paths.add(prefix)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"available_fields", "field_sources"}:
                continue
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.update(_available_paths(item, child))
    return paths


def _normalise_vec3(value: Any, *, label: str) -> dict[str, float]:
    if isinstance(value, Mapping):
        keys = {str(key).casefold(): item for key, item in value.items()}
        try:
            return {axis: float(keys[axis]) for axis in ("x", "y", "z")}
        except (KeyError, TypeError, ValueError) as exc:
            raise SceneModelError("INVALID_VECTOR", f"{label} 必须包含数值 x/y/z") from exc
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) == 3:
            try:
                return {axis: float(item) for axis, item in zip(("x", "y", "z"), value)}
            except (TypeError, ValueError) as exc:
                raise SceneModelError("INVALID_VECTOR", f"{label} 必须是三维数值") from exc
    raise SceneModelError("INVALID_VECTOR", f"{label} 必须是三维向量")


def _normalise_entity(
    value: Mapping[str, Any],
    *,
    level_id: str,
    default_stability_scope: str,
) -> dict[str, Any]:
    entity_id = str(value.get("entity_id") or "").strip()
    if not entity_id:
        raise SceneModelError("ENTITY_ID_MISSING", "场景实体缺少稳定 entity_id")
    entity: dict[str, Any] = {
        "entity_id": entity_id,
        "stability_scope": str(
            value.get("stability_scope") or default_stability_scope
        ).strip(),
        "level_id": str(value.get("level_id") or level_id).strip(),
    }
    if not entity["level_id"]:
        raise SceneModelError("LEVEL_ID_MISSING", f"实体 {entity_id} 缺少 level_id")

    for key in ("name", "class_name", "asset_reference", "parent_id"):
        if key in value and value[key] is not None:
            entity[key] = str(value[key])
    if "tags" in value and value["tags"] is not None:
        if not isinstance(value["tags"], list):
            raise SceneModelError("INVALID_ENTITY_FIELD", f"实体 {entity_id} 的 tags 必须是数组")
        entity["tags"] = [str(item) for item in value["tags"]]
    if "components" in value and value["components"] is not None:
        if not isinstance(value["components"], list):
            raise SceneModelError(
                "INVALID_ENTITY_FIELD", f"实体 {entity_id} 的 components 必须是数组"
            )
        entity["components"] = _json_copy(value["components"])
    if "properties" in value and value["properties"] is not None:
        if not isinstance(value["properties"], Mapping):
            raise SceneModelError(
                "INVALID_ENTITY_FIELD", f"实体 {entity_id} 的 properties 必须是对象"
            )
        entity["properties"] = _json_copy(value["properties"])

    if "transform" in value and value["transform"] is not None:
        raw_transform = value["transform"]
        if not isinstance(raw_transform, Mapping):
            raise SceneModelError("INVALID_ENTITY_FIELD", f"实体 {entity_id} 的 transform 必须是对象")
        transform: dict[str, Any] = {}
        for key in ("location", "rotation", "scale"):
            if key in raw_transform and raw_transform[key] is not None:
                transform[key] = _normalise_vec3(
                    raw_transform[key], label=f"{entity_id}.transform.{key}"
                )
        entity["transform"] = transform

    if "bounds" in value and value["bounds"] is not None:
        raw_bounds = value["bounds"]
        if not isinstance(raw_bounds, Mapping):
            raise SceneModelError("INVALID_ENTITY_FIELD", f"实体 {entity_id} 的 bounds 必须是对象")
        bounds: dict[str, Any] = {}
        for key in ("origin", "extent"):
            if key in raw_bounds and raw_bounds[key] is not None:
                bounds[key] = _normalise_vec3(raw_bounds[key], label=f"{entity_id}.bounds.{key}")
        entity["bounds"] = bounds

    paths = _available_paths(entity)
    if any(path in paths for path in {"name", "class_name", "entity_id"}):
        paths.add("identity")
    entity["available_fields"] = sorted(paths - {"available_fields", "field_sources"})
    raw_sources = value.get("field_sources")
    if raw_sources is not None:
        if not isinstance(raw_sources, Mapping):
            raise SceneModelError("INVALID_ENTITY_FIELD", "field_sources 必须是对象")
        entity["field_sources"] = {str(key): str(item) for key, item in raw_sources.items()}
    return entity


@dataclass(frozen=True)
class SceneSnapshot:
    snapshot_id: str
    content_hash: str
    source: str
    level_id: str
    captured_at: str
    query: dict[str, Any]
    completeness: dict[str, Any]
    coordinate_system: str
    linear_unit: str
    angle_unit: str
    entities: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneSnapshot":
        document = _json_copy(dict(value))
        errors = sorted(
            _validator().iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            error = errors[0]
            path = ".".join(str(part) for part in error.absolute_path)
            raise SceneModelError(
                "INVALID_SCENE_SNAPSHOT",
                f"SceneSnapshot 不满足 Schema（字段 {path or '<root>'}）：{error.message}",
                details={"path": path, "validator": error.validator},
            )
        entity_ids = [item["entity_id"] for item in document["entities"]]
        if len(entity_ids) != len(set(entity_ids)):
            raise SceneModelError("DUPLICATE_ENTITY_ID", "快照中的 entity_id 不能重复")
        actual_hash = snapshot_content_hash(document)
        if document["content_hash"] != actual_hash:
            raise SceneModelError("SNAPSHOT_HASH_MISMATCH", "快照内容哈希不一致")
        if document["snapshot_id"] != f"snapshot_{actual_hash[:16]}":
            raise SceneModelError("SNAPSHOT_HASH_MISMATCH", "snapshot_id 与内容哈希不一致")
        return cls(
            snapshot_id=document["snapshot_id"],
            content_hash=document["content_hash"],
            source=document["source"],
            level_id=document["level_id"],
            captured_at=document["captured_at"],
            query=document["query"],
            completeness=document["completeness"],
            coordinate_system=document["coordinate_system"],
            linear_unit=document["linear_unit"],
            angle_unit=document["angle_unit"],
            entities=tuple(document["entities"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "content_hash": self.content_hash,
            "source": self.source,
            "level_id": self.level_id,
            "captured_at": self.captured_at,
            "query": _json_copy(self.query),
            "completeness": _json_copy(self.completeness),
            "coordinate_system": self.coordinate_system,
            "linear_unit": self.linear_unit,
            "angle_unit": self.angle_unit,
            "entities": _json_copy(self.entities),
        }


class SceneSnapshotBuilder:
    """把一组 Fake UE 或真实 UE 分页响应合并为标准快照。"""

    def build(
        self,
        query: Mapping[str, Any],
        pages: Sequence[Mapping[str, Any]],
        *,
        captured_at: str | None = None,
    ) -> SceneSnapshot:
        if not pages:
            raise SceneModelError("EMPTY_SCENE_RESPONSE", "场景查询没有返回任何分页")
        first = pages[0]
        level_id = str(first.get("level_id") or "").strip()
        coordinate_system = str(first.get("coordinate_system") or "").strip()
        linear_unit = str(first.get("linear_unit") or "").strip()
        angle_unit = str(first.get("angle_unit") or "").strip()
        if not all((level_id, coordinate_system, linear_unit, angle_unit)):
            raise SceneModelError(
                "SCENE_METADATA_MISSING",
                "场景响应必须声明 level_id、坐标系、长度单位和角度单位",
            )

        entities_by_id: dict[str, dict[str, Any]] = {}
        missing_fields: set[str] = set()
        truncated = False
        total_count: int | None = None
        next_cursor: Any = None
        default_scope = str(first.get("id_stability_scope") or "query")
        for page_index, page in enumerate(pages, start=1):
            for key, expected in (
                ("level_id", level_id),
                ("coordinate_system", coordinate_system),
                ("linear_unit", linear_unit),
                ("angle_unit", angle_unit),
            ):
                if str(page.get(key) or "").strip() != expected:
                    raise SceneModelError(
                        "SCENE_PAGE_METADATA_MISMATCH",
                        f"第 {page_index} 页的 {key} 与第一页不一致",
                    )
            raw_entities = page.get("entities")
            if not isinstance(raw_entities, list):
                raise SceneModelError("INVALID_SCENE_PAGE", "场景分页的 entities 必须是数组")
            for raw_entity in raw_entities:
                if not isinstance(raw_entity, Mapping):
                    raise SceneModelError("INVALID_SCENE_PAGE", "场景实体必须是对象")
                entity = _normalise_entity(
                    raw_entity,
                    level_id=level_id,
                    default_stability_scope=default_scope,
                )
                entity_id = entity["entity_id"]
                if entity_id in entities_by_id and entities_by_id[entity_id] != entity:
                    raise SceneModelError(
                        "DUPLICATE_ENTITY_CONFLICT",
                        f"分页中实体 {entity_id} 的内容不一致",
                    )
                entities_by_id[entity_id] = entity
            missing_fields.update(str(item) for item in page.get("missing_fields") or [])
            truncated = truncated or bool(page.get("truncated", False))
            if page.get("total_count") is not None:
                current_total = int(page["total_count"])
                if total_count is not None and current_total != total_count:
                    raise SceneModelError("SCENE_TOTAL_MISMATCH", "分页 total_count 不一致")
                total_count = current_total
            next_cursor = page.get("next_cursor")

        returned_count = len(entities_by_id)
        incomplete_count = total_count is not None and returned_count < total_count
        complete = not truncated and not missing_fields and not next_cursor and not incomplete_count
        document: dict[str, Any] = {
            "snapshot_id": "snapshot_pending",
            "content_hash": "0" * 64,
            "source": str(first.get("source") or "unreal_editor"),
            "level_id": level_id,
            "captured_at": captured_at or utc_now(),
            "query": _json_copy(dict(query)),
            "completeness": {
                "complete": complete,
                "truncated": bool(truncated or incomplete_count or next_cursor),
                "missing_fields": sorted(missing_fields),
                "returned_count": returned_count,
                "total_count": total_count,
                "page_count": len(pages),
            },
            "coordinate_system": coordinate_system,
            "linear_unit": linear_unit,
            "angle_unit": angle_unit,
            "entities": [entities_by_id[key] for key in sorted(entities_by_id)],
        }
        content_hash = snapshot_content_hash(document)
        document["content_hash"] = content_hash
        document["snapshot_id"] = f"snapshot_{content_hash[:16]}"
        return SceneSnapshot.from_dict(document)
