"""UE 工程资源注册表。

注册表保存 UE Asset Registry 的权威对象路径，以及模型建议和用户确认结果。
它不是普通向量库：只有 ``confirmed`` 且本次扫描仍存在的资源，才能作为
生成脚本的可执行路径候选。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from knowledge_base import DEFAULT_DATA_ROOT


USER_STATUSES = {"pending", "confirmed", "rejected"}


class ResourceRegistryError(RuntimeError):
    """资源注册表输入或持久化错误。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[,，;；\n]+", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = [str(item) for item in value]
    else:
        raise ResourceRegistryError("用途字段必须是字符串或字符串列表")
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


class ResourceRegistry:
    def __init__(
        self,
        workspace_id: str,
        *,
        data_root: Path | str = DEFAULT_DATA_ROOT,
    ) -> None:
        if not workspace_id.strip():
            raise ResourceRegistryError("workspace_id 不能为空")
        self.workspace_id = workspace_id
        self.root = Path(data_root) / workspace_id / "resources"
        self.database_path = self.root / "resources.db"
        self.root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        """提交或回滚后立即关闭数据库句柄，避免 Windows 文件锁残留。"""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY,
                    object_path TEXT NOT NULL UNIQUE,
                    package_path TEXT NOT NULL,
                    asset_name TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    purposes_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL,
                    scan_revision TEXT NOT NULL,
                    scan_status TEXT NOT NULL,
                    user_status TEXT NOT NULL,
                    exists_in_project INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_resources_status
                    ON resources(user_status, exists_in_project);
                """
            )

    @staticmethod
    def _validate_object_path(object_path: str) -> str:
        path = object_path.strip().replace("\\", "/")
        if not path.startswith("/") or "/" not in path[1:]:
            raise ResourceRegistryError(f"UE 对象路径格式无效：{object_path}")
        if any(char in path for char in {"\n", "\r", "\t"}):
            raise ResourceRegistryError("UE 对象路径不能包含控制字符")
        return path

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["purposes"] = json.loads(item.pop("purposes_json") or "[]")
        item["exists_in_project"] = bool(item["exists_in_project"])
        return item

    def ingest_scan(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        scan_revision: str | None = None,
        source: str = "ue_asset_registry",
        mark_missing: bool = True,
    ) -> dict[str, Any]:
        """导入一次 UE 权威扫描。

        扫描只证明资源存在，不会自动变成 ``confirmed``。已有用户确认信息会
        在增量扫描中保留；不在新扫描里的旧资源标记为不存在。
        """

        revision = (scan_revision or f"scan-{uuid.uuid4().hex[:12]}").strip()
        if not revision:
            raise ResourceRegistryError("scan_revision 不能为空")
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in records:
            object_path = self._validate_object_path(str(raw.get("object_path", "")))
            if object_path in seen:
                raise ResourceRegistryError(f"扫描结果包含重复对象路径：{object_path}")
            seen.add(object_path)
            package_path = str(raw.get("package_path") or object_path.rsplit("/", 1)[0]).strip()
            asset_name = str(
                raw.get("asset_name")
                or object_path.rsplit("/", 1)[-1].split(".", 1)[0]
            ).strip()
            asset_class = str(raw.get("asset_class") or raw.get("class") or "Unknown").strip()
            if not package_path or not asset_name or not asset_class:
                raise ResourceRegistryError(f"资源字段不完整：{object_path}")
            user_status = str(raw.get("user_status") or "pending").strip().casefold()
            if user_status not in USER_STATUSES:
                raise ResourceRegistryError(f"用户状态无效：{user_status}")
            prepared.append(
                {
                    "object_path": object_path,
                    "package_path": package_path,
                    "asset_name": asset_name,
                    "asset_class": asset_class,
                    "description": str(raw.get("description") or "").strip(),
                    "purposes": _normalise_list(raw.get("purposes")),
                    "user_status": user_status,
                }
            )

        now = _now()
        inserted = updated = 0
        with self._connection() as connection:
            if mark_missing:
                connection.execute(
                    "UPDATE resources SET exists_in_project = 0, scan_status = 'missing', updated_at = ?",
                    (now,),
                )
            for item in prepared:
                existing = connection.execute(
                    "SELECT * FROM resources WHERE object_path = ?",
                    (item["object_path"],),
                ).fetchone()
                if existing:
                    # 已确认的人工语义优先于后续模型建议；扫描只更新 UE 权威字段。
                    keep_human_data = existing["user_status"] in {"confirmed", "rejected"}
                    connection.execute(
                        """
                        UPDATE resources SET
                            package_path = ?, asset_name = ?, asset_class = ?,
                            description = ?, purposes_json = ?, source = ?,
                            scan_revision = ?, scan_status = 'present',
                            user_status = ?, exists_in_project = 1, updated_at = ?
                        WHERE object_path = ?
                        """,
                        (
                            item["package_path"],
                            item["asset_name"],
                            item["asset_class"],
                            existing["description"] if keep_human_data else item["description"],
                            existing["purposes_json"]
                            if keep_human_data
                            else json.dumps(item["purposes"], ensure_ascii=False),
                            source,
                            revision,
                            existing["user_status"] if keep_human_data else item["user_status"],
                            now,
                            item["object_path"],
                        ),
                    )
                    updated += 1
                else:
                    connection.execute(
                        """
                        INSERT INTO resources(
                            id, object_path, package_path, asset_name, asset_class,
                            description, purposes_json, source, scan_revision,
                            scan_status, user_status, exists_in_project, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, 1, ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            item["object_path"],
                            item["package_path"],
                            item["asset_name"],
                            item["asset_class"],
                            item["description"],
                            json.dumps(item["purposes"], ensure_ascii=False),
                            source,
                            revision,
                            item["user_status"],
                            now,
                            now,
                        ),
                    )
                    inserted += 1
        return {
            "scan_revision": revision,
            "received": len(prepared),
            "inserted": inserted,
            "updated": updated,
            "marked_missing": mark_missing,
        }

    def list_resources(
        self,
        *,
        user_status: str | None = None,
        include_missing: bool = True,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if user_status:
            if user_status not in USER_STATUSES:
                raise ResourceRegistryError(f"用户状态无效：{user_status}")
            conditions.append("user_status = ?")
            params.append(user_status)
        if not include_missing:
            conditions.append("exists_in_project = 1")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM resources" + where + " ORDER BY asset_class, asset_name",
                params,
            ).fetchall()
        return [self._to_dict(row) for row in rows]

    def update_confirmation(
        self,
        resource_id: str,
        *,
        user_status: str,
        description: str | None = None,
        purposes: Sequence[str] | str | None = None,
    ) -> dict[str, Any]:
        status = user_status.strip().casefold()
        if status not in USER_STATUSES:
            raise ResourceRegistryError(f"用户状态无效：{status}")
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM resources WHERE id = ?", (resource_id,)
            ).fetchone()
            if not existing:
                raise ResourceRegistryError(f"资源不存在：{resource_id}")
            new_description = (
                str(description).strip() if description is not None else existing["description"]
            )
            new_purposes = (
                _normalise_list(purposes)
                if purposes is not None
                else json.loads(existing["purposes_json"] or "[]")
            )
            connection.execute(
                """
                UPDATE resources
                SET user_status = ?, description = ?, purposes_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    new_description,
                    json.dumps(new_purposes, ensure_ascii=False),
                    _now(),
                    resource_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM resources WHERE id = ?", (resource_id,)
            ).fetchone()
        return self._to_dict(row)

    def apply_folder_report(self, report: Mapping[str, Any]) -> dict[str, int]:
        """批准文件夹报告后，让现存资产继承所在目录的语义和确认状态。"""

        raw_folders = report.get("folders")
        if not isinstance(raw_folders, list) or not raw_folders:
            raise ResourceRegistryError("文件夹报告缺少 folders")
        folder_map: dict[str, Mapping[str, Any]] = {}
        for item in raw_folders:
            if not isinstance(item, Mapping):
                raise ResourceRegistryError("文件夹报告包含无效条目")
            path = str(item.get("folder_path") or "").rstrip("/")
            if not path.startswith("/"):
                raise ResourceRegistryError(f"文件夹路径无效：{path}")
            folder_map[path] = item

        confirmed = skipped = 0
        now = _now()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, package_path FROM resources WHERE exists_in_project = 1"
            ).fetchall()
            for row in rows:
                package_path = str(row["package_path"]).rstrip("/")
                classification = folder_map.get(package_path)
                if classification is None:
                    # 正常报告应覆盖每个资产直属文件夹；缺失时保持 pending 更安全。
                    skipped += 1
                    continue
                purpose = str(classification.get("purpose") or "用途待确认").strip()
                raw_categories = classification.get("asset_categories", [])
                categories = (
                    [str(item).strip() for item in raw_categories if str(item).strip()]
                    if isinstance(raw_categories, list)
                    else []
                )
                purposes = list(dict.fromkeys([purpose, *categories]))
                description = f"所属文件夹 {package_path}：{purpose}"
                notes = str(classification.get("notes") or "").strip()
                if notes:
                    description += f"；{notes}"
                connection.execute(
                    """
                    UPDATE resources
                    SET user_status = 'confirmed', description = ?, purposes_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        description,
                        json.dumps(purposes, ensure_ascii=False),
                        now,
                        row["id"],
                    ),
                )
                confirmed += 1
        return {"confirmed": confirmed, "skipped": skipped}

    def search(
        self,
        query: str,
        *,
        asset_class: str | None = None,
        confirmed_only: bool = True,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """确定性词法检索；执行态默认只返回已确认且仍存在的对象路径。"""

        clean_query = query.strip().casefold()
        if not clean_query:
            raise ResourceRegistryError("资源检索问题不能为空")
        if not 1 <= limit <= 100:
            raise ResourceRegistryError("limit 必须在 1 到 100 之间")
        conditions = ["exists_in_project = 1"]
        params: list[Any] = []
        if confirmed_only:
            conditions.append("user_status = 'confirmed'")
        if asset_class:
            conditions.append("LOWER(asset_class) = ?")
            params.append(asset_class.strip().casefold())
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM resources WHERE " + " AND ".join(conditions),
                params,
            ).fetchall()

        tokens = [token for token in re.split(r"\s+", clean_query) if token]
        results: list[dict[str, Any]] = []
        for row in rows:
            item = self._to_dict(row)
            fields = {
                "asset_name": item["asset_name"].casefold(),
                "description": item["description"].casefold(),
                "purposes": " ".join(item["purposes"]).casefold(),
                "asset_class": item["asset_class"].casefold(),
                "object_path": item["object_path"].casefold(),
            }
            weights = {
                "asset_name": 5.0,
                "description": 4.0,
                "purposes": 4.0,
                "asset_class": 2.0,
                "object_path": 1.0,
            }
            score = 0.0
            for token in tokens:
                for field, content in fields.items():
                    if token in content:
                        score += weights[field]
            if clean_query in " ".join(fields.values()):
                score += 3.0
            if score > 0:
                item["score"] = score
                results.append(item)
        return sorted(
            results,
            key=lambda item: (-item["score"], item["asset_name"].casefold()),
        )[:limit]

    def stats(self) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN user_status = 'pending' AND exists_in_project = 1 THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN user_status = 'confirmed' AND exists_in_project = 1 THEN 1 ELSE 0 END) AS confirmed,
                    SUM(CASE WHEN user_status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                    SUM(CASE WHEN exists_in_project = 0 THEN 1 ELSE 0 END) AS missing
                FROM resources
                """
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}
