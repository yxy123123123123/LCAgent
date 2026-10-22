"""工作区级快照、差异、验证报告和修复范围存储。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from constraint_engine import ValidationReport
from knowledge_base import DEFAULT_DATA_ROOT
from repair_controller import RepairScope
from scene_diff import SceneDiff
from scene_models import SceneSnapshot


class SceneStoreError(RuntimeError):
    """场景记录读写错误。"""


class SceneRecordStore:
    KINDS = {
        "snapshots": ("snapshot_", SceneSnapshot),
        "diffs": ("diff_", SceneDiff),
        "validations": ("validation_", ValidationReport),
        "repair_scopes": ("repair_", RepairScope),
    }

    def __init__(
        self,
        workspace_id: str,
        *,
        data_root: Path | str = DEFAULT_DATA_ROOT,
    ) -> None:
        if not workspace_id.strip():
            raise SceneStoreError("workspace_id 不能为空")
        self.workspace_id = workspace_id
        self.root = Path(data_root) / workspace_id / "scene_records"
        for kind in self.KINDS:
            (self.root / kind).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=".scene-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _save(self, kind: str, record_id: str, document: Mapping[str, Any]) -> Path:
        prefix, record_type = self.KINDS[kind]
        if not record_id.startswith(prefix) or Path(record_id).name != record_id:
            raise SceneStoreError(f"{kind} 记录 ID 无效")
        validated = record_type.from_dict(document)
        path = self.root / kind / f"{record_id}.json"
        self._atomic_write(path, validated.as_dict())
        return path

    def save_snapshot(self, snapshot: SceneSnapshot) -> Path:
        return self._save("snapshots", snapshot.snapshot_id, snapshot.as_dict())

    def save_diff(self, scene_diff: SceneDiff) -> Path:
        return self._save("diffs", scene_diff.diff_id, scene_diff.as_dict())

    def save_validation(self, report: ValidationReport) -> Path:
        return self._save("validations", report.report_id, report.as_dict())

    def save_repair_scope(self, scope: RepairScope) -> Path:
        return self._save("repair_scopes", scope.repair_id, scope.as_dict())

    def list_records(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for kind, (_prefix, record_type) in self.KINDS.items():
            items: list[dict[str, Any]] = []
            for path in sorted((self.root / kind).glob("*.json")):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    record = record_type.from_dict(document)
                except Exception as exc:
                    raise SceneStoreError(f"场景记录损坏：{path.name}") from exc
                items.append(record.as_dict())
            result[kind] = items
        return result
