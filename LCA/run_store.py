"""SQLite 运行记录与文件产物存储。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from run_models import RunEvent, RunStatus, TaskRun, utc_now


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_DATA_ROOT = PROJECT_ROOT / ".lca_data"
DEFAULT_RUN_DB_PATH = DEFAULT_RUN_DATA_ROOT / "lca.db"
SENSITIVE_KEY_PARTS = ("api_key", "authorization", "secret", "access_token", "refresh_token")


class RunStoreError(RuntimeError):
    """运行数据库或产物错误。"""


def redact_sensitive(value: Any) -> Any:
    """递归清除运行产物中的常见密钥字段。"""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "***REDACTED***"
            else:
                redacted[str(key)] = redact_sensitive(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value


class RunStore:
    """以短事务保存运行和事件，每次操作使用独立 SQLite 连接。"""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_RUN_DB_PATH,
        *,
        artifacts_root: Path | str | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.artifacts_root = Path(
            artifacts_root or self.db_path.parent / "runs"
        ).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """SQLite 原生上下文不会关闭连接，这里统一提交、回滚并释放句柄。"""

        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_text TEXT NOT NULL,
                    model_profile_id TEXT,
                    embedding_profile_id TEXT,
                    execution_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    termination_reason TEXT,
                    final_answer TEXT,
                    error_json TEXT,
                    budgets_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_runs_workspace_created
                    ON runs(workspace_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_artifacts (
                    run_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, artifact_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(redact_sensitive(value), ensure_ascii=False, separators=(",", ":"))

    def create_run(self, run: TaskRun) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, workspace_id, task_text, model_profile_id,
                        embedding_profile_id, execution_mode, status, created_at,
                        started_at, finished_at, termination_reason, final_answer,
                        error_json, budgets_json, metrics_json, cancel_requested
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.workspace_id,
                        run.task_text,
                        run.model_profile_id,
                        run.embedding_profile_id,
                        run.execution_mode,
                        run.status,
                        run.created_at,
                        run.started_at,
                        run.finished_at,
                        run.termination_reason,
                        run.final_answer,
                        self._json(run.error) if run.error is not None else None,
                        self._json(run.budgets),
                        self._json(run.metrics),
                        int(run.cancel_requested),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RunStoreError(f"运行记录已存在：{run.run_id}") from exc

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> TaskRun:
        return TaskRun(
            run_id=row["run_id"],
            workspace_id=row["workspace_id"],
            task_text=row["task_text"],
            model_profile_id=row["model_profile_id"],
            embedding_profile_id=row["embedding_profile_id"],
            execution_mode=row["execution_mode"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            termination_reason=row["termination_reason"],
            final_answer=row["final_answer"],
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            budgets=json.loads(row["budgets_json"]),
            metrics=json.loads(row["metrics_json"]),
            cancel_requested=bool(row["cancel_requested"]),
        )

    def get_run(self, run_id: str) -> TaskRun:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunStoreError(f"未找到运行：{run_id}")
        return self._run_from_row(row)

    def list_runs(self, workspace_id: str | None = None, *, limit: int = 100) -> list[TaskRun]:
        clean_limit = max(1, min(int(limit), 500))
        with self._connection() as connection:
            if workspace_id:
                rows = connection.execute(
                    "SELECT * FROM runs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
                    (workspace_id, clean_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (clean_limit,)
                ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def update_run(self, run_id: str, **changes: Any) -> TaskRun:
        allowed = {
            "status",
            "started_at",
            "finished_at",
            "termination_reason",
            "final_answer",
            "error",
            "metrics",
            "cancel_requested",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise RunStoreError(f"不允许更新运行字段：{', '.join(unknown)}")
        if not changes:
            return self.get_run(run_id)
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            column = {"error": "error_json", "metrics": "metrics_json"}.get(key, key)
            if key in {"error", "metrics"}:
                value = self._json(value) if value is not None else None
            elif key == "cancel_requested":
                value = int(bool(value))
            assignments.append(f"{column} = ?")
            values.append(value)
        values.append(run_id)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {', '.join(assignments)} WHERE run_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise RunStoreError(f"未找到运行：{run_id}")
        return self.get_run(run_id)

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        phase: str,
        status: str,
        summary: Mapping[str, Any] | None = None,
        artifact_refs: list[str] | tuple[str, ...] = (),
    ) -> RunEvent:
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                raise RunStoreError(f"未找到运行：{run_id}")
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            event = RunEvent(
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                phase=phase,
                status=status,
                timestamp=timestamp,
                summary=redact_sensitive(dict(summary or {})),
                artifact_refs=tuple(artifact_refs),
            )
            connection.execute(
                """
                INSERT INTO run_events (
                    run_id, sequence, event_type, phase, status, timestamp,
                    summary_json, artifact_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    event.sequence,
                    event.event_type,
                    event.phase,
                    event.status,
                    event.timestamp,
                    self._json(event.summary),
                    self._json(list(event.artifact_refs)),
                ),
            )
        return event

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[RunEvent]:
        self.get_run(run_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (run_id, max(0, int(after_sequence))),
            ).fetchall()
        return [
            RunEvent(
                run_id=row["run_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                phase=row["phase"],
                status=row["status"],
                timestamp=row["timestamp"],
                summary=json.loads(row["summary_json"]),
                artifact_refs=tuple(json.loads(row["artifact_refs_json"])),
            )
            for row in rows
        ]

    def request_cancel(self, run_id: str) -> TaskRun:
        terminal_statuses = {status.value for status in RunStatus if status.terminal}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RunStoreError(f"未找到运行：{run_id}")
            if row["status"] not in terminal_statuses:
                connection.execute(
                    """
                    UPDATE runs
                    SET cancel_requested = 1, status = ?
                    WHERE run_id = ?
                    """,
                    (RunStatus.CANCELLING.value, run_id),
                )
                sequence = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO run_events (
                        run_id, sequence, event_type, phase, status, timestamp,
                        summary_json, artifact_refs_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        sequence,
                        "cancel_requested",
                        "CANCEL",
                        "pending",
                        utc_now(),
                        self._json({}),
                        self._json([]),
                    ),
                )
        return self.get_run(run_id)

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get_run(run_id).cancel_requested

    def _run_directory(self, run_id: str) -> Path:
        # run_id 由系统生成；这里仍做文件名约束，避免路径穿越。
        if not run_id.startswith("run_") or Path(run_id).name != run_id:
            raise RunStoreError("run_id 格式无效")
        path = (self.artifacts_root / run_id / "artifacts").resolve()
        root = self.artifacts_root.resolve()
        if root not in path.parents:
            raise RunStoreError("运行产物目录越界")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json_artifact(self, run_id: str, artifact_id: str, value: Any) -> dict[str, Any]:
        safe_name = Path(artifact_id).name
        if safe_name != artifact_id or not safe_name.endswith(".json"):
            raise RunStoreError("JSON 产物名称必须是不含目录的 .json 文件名")
        payload = (
            json.dumps(redact_sensitive(value), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        target = self._run_directory(run_id) / safe_name
        fd, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        relative_path = target.relative_to(self.artifacts_root).as_posix()
        record = {
            "run_id": run_id,
            "artifact_id": safe_name,
            "relative_path": relative_path,
            "media_type": "application/json",
            "byte_size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "created_at": utc_now(),
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO run_artifacts (
                    run_id, artifact_id, relative_path, media_type,
                    byte_size, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(record[key] for key in (
                    "run_id", "artifact_id", "relative_path", "media_type",
                    "byte_size", "sha256", "created_at"
                )),
            )
        return record

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM run_artifacts WHERE run_id = ? ORDER BY created_at, artifact_id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def read_json_artifact(self, run_id: str, artifact_id: str) -> Any:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT relative_path FROM run_artifacts WHERE run_id = ? AND artifact_id = ?",
                (run_id, artifact_id),
            ).fetchone()
        if row is None:
            raise RunStoreError(f"未找到运行产物：{artifact_id}")
        path = (self.artifacts_root / row["relative_path"]).resolve()
        if self.artifacts_root.resolve() not in path.parents:
            raise RunStoreError("运行产物路径越界")
        return json.loads(path.read_text(encoding="utf-8"))
