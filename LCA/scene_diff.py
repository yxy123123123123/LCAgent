"""标准 SceneSnapshot 的确定性结构化差异。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from goal_models import canonical_hash
from scene_models import SceneSnapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIFF_SCHEMA_PATH = PROJECT_ROOT / "contracts" / "scene" / "scene_diff.schema.json"
IGNORED_ENTITY_FIELDS = {"available_fields", "field_sources"}


class SceneDiffError(RuntimeError):
    """快照无法比较或差异文档无效。"""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            if not prefix and key in IGNORED_ENTITY_FIELDS:
                continue
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child))
    else:
        result[prefix] = value
    return result


def _tolerance_for(path: str, tolerances: Mapping[str, float]) -> float:
    matches = [
        (key, float(value))
        for key, value in tolerances.items()
        if path == key or path.startswith(f"{key}.")
    ]
    if matches:
        return max(matches, key=lambda item: len(item[0]))[1]
    if path.startswith("transform.location") or path.startswith("bounds"):
        return float(tolerances.get("position_cm", 0.0))
    if path.startswith("transform.rotation"):
        return float(tolerances.get("angle_degree", 0.0))
    if path.startswith("transform.scale"):
        return float(tolerances.get("scale", 0.0))
    return float(tolerances.get("default", 0.0))


def _equal(before: Any, after: Any, tolerance: float) -> bool:
    if isinstance(before, (int, float)) and not isinstance(before, bool):
        if isinstance(after, (int, float)) and not isinstance(after, bool):
            return math.isclose(float(before), float(after), abs_tol=tolerance, rel_tol=0.0)
    return before == after


@dataclass(frozen=True)
class SceneDiff:
    diff_id: str
    before_snapshot_id: str
    after_snapshot_id: str
    status: str
    reasons: tuple[str, ...]
    added_entities: tuple[str, ...]
    removed_entities: tuple[str, ...]
    modified_entities: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneDiff":
        document = _json_copy(dict(value))
        schema = json.loads(DEFAULT_DIFF_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            error = errors[0]
            raise SceneDiffError(f"SceneDiff 不满足 Schema：{error.message}")
        body = dict(document)
        body.pop("diff_id")
        expected = f"diff_{canonical_hash(body)[:16]}"
        if document["diff_id"] != expected:
            raise SceneDiffError("SceneDiff 内容与 diff_id 不一致")
        return cls(
            diff_id=document["diff_id"],
            before_snapshot_id=document["before_snapshot_id"],
            after_snapshot_id=document["after_snapshot_id"],
            status=document["status"],
            reasons=tuple(document["reasons"]),
            added_entities=tuple(document["added_entities"]),
            removed_entities=tuple(document["removed_entities"]),
            modified_entities=tuple(document["modified_entities"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "diff_id": self.diff_id,
            "before_snapshot_id": self.before_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "added_entities": list(self.added_entities),
            "removed_entities": list(self.removed_entities),
            "modified_entities": _json_copy(self.modified_entities),
        }


def diff_snapshots(
    before: SceneSnapshot,
    after: SceneSnapshot,
    *,
    tolerances: Mapping[str, float] | None = None,
) -> SceneDiff:
    before = SceneSnapshot.from_dict(before.as_dict())
    after = SceneSnapshot.from_dict(after.as_dict())
    tolerance_map = {str(key): float(value) for key, value in dict(tolerances or {}).items()}
    reasons: list[str] = []
    for label, snapshot in (("before", before), ("after", after)):
        if not snapshot.completeness["complete"]:
            reasons.append(f"{label}_snapshot_incomplete")
    if before.level_id != after.level_id:
        reasons.append("level_id_mismatch")
    if before.coordinate_system != after.coordinate_system:
        reasons.append("coordinate_system_mismatch")
    if before.linear_unit != after.linear_unit:
        reasons.append("linear_unit_mismatch")
    if before.angle_unit != after.angle_unit:
        reasons.append("angle_unit_mismatch")

    before_entities = {item["entity_id"]: item for item in before.entities}
    after_entities = {item["entity_id"]: item for item in after.entities}
    before_ids = set(before_entities)
    after_ids = set(after_entities)
    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    modified: list[dict[str, Any]] = []

    for entity_id in sorted(before_ids & after_ids):
        before_entity = before_entities[entity_id]
        after_entity = after_entities[entity_id]
        if before_entity["stability_scope"] == "query" or after_entity["stability_scope"] == "query":
            reasons.append(f"unstable_entity_id:{entity_id}")
        before_flat = _flatten(before_entity)
        after_flat = _flatten(after_entity)
        missing_paths = sorted(set(before_flat) ^ set(after_flat))
        if missing_paths:
            reasons.append(f"field_set_mismatch:{entity_id}")
        changes: list[dict[str, Any]] = []
        for path in sorted(set(before_flat) & set(after_flat)):
            old = before_flat[path]
            new = after_flat[path]
            tolerance = _tolerance_for(path, tolerance_map)
            if _equal(old, new, tolerance):
                continue
            change: dict[str, Any] = {"path": path, "before": old, "after": new}
            if (
                isinstance(old, (int, float))
                and not isinstance(old, bool)
                and isinstance(new, (int, float))
                and not isinstance(new, bool)
            ):
                change["delta"] = float(new) - float(old)
                change["tolerance"] = tolerance
            changes.append(change)
        if changes:
            modified.append({"entity_id": entity_id, "changes": changes})

    body: dict[str, Any] = {
        "before_snapshot_id": before.snapshot_id,
        "after_snapshot_id": after.snapshot_id,
        "status": "inconclusive" if reasons else "comparable",
        "reasons": sorted(set(reasons)),
        "added_entities": added,
        "removed_entities": removed,
        "modified_entities": modified,
    }
    body["diff_id"] = f"diff_{canonical_hash(body)[:16]}"
    return SceneDiff.from_dict(body)
