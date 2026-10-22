"""前端工作区与对话的本地存储。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DEFAULT_WORKSPACES_PATH = APP_DIR / "workspaces.local.json"
DEFAULT_CHATS_PATH = PROJECT_ROOT / ".codex_tmp" / "workspace_chats.json"


class WorkspaceError(RuntimeError):
    """工作区配置错误。"""


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    name: str
    path: str
    last_opened_at: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Workspace":
        return cls(
            workspace_id=str(data["workspace_id"]),
            name=str(data["name"]),
            path=str(data["path"]),
            last_opened_at=str(data.get("last_opened_at", "")),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_id(path: Path) -> str:
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:16]


class WorkspaceStore:
    def __init__(self, path: Path | str = DEFAULT_WORKSPACES_PATH) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "selected_workspace_id": None, "workspaces": []}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"无法读取工作区配置：{exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("workspaces"), list):
            raise WorkspaceError("工作区配置文件格式错误")
        return document

    def _save(self, document: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def add(self, path: str, name: str | None = None) -> Workspace:
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.is_dir():
            raise WorkspaceError(f"工作区目录不存在：{resolved}")
        workspace = Workspace(
            workspace_id=_workspace_id(resolved),
            name=(name or resolved.name or str(resolved)).strip(),
            path=str(resolved),
            last_opened_at=_now(),
        )
        document = self._load()
        items = [
            item for item in document["workspaces"]
            if item.get("workspace_id") != workspace.workspace_id
        ]
        items.append(asdict(workspace))
        document["workspaces"] = items
        document["selected_workspace_id"] = workspace.workspace_id
        self._save(document)
        return workspace

    def ensure_default(self, path: str) -> Workspace:
        workspaces = self.list()
        if workspaces:
            selected_id = self._load().get("selected_workspace_id")
            return next(
                (item for item in workspaces if item.workspace_id == selected_id),
                workspaces[0],
            )
        return self.add(path)

    def list(self) -> list[Workspace]:
        workspaces = [Workspace.from_dict(item) for item in self._load()["workspaces"]]
        return sorted(workspaces, key=lambda item: item.last_opened_at, reverse=True)

    def select(self, workspace_id: str) -> Workspace:
        document = self._load()
        for index, item in enumerate(document["workspaces"]):
            if item.get("workspace_id") == workspace_id:
                resolved = Path(str(item["path"]))
                if not resolved.is_dir():
                    raise WorkspaceError(f"工作区已不存在：{resolved}")
                item["last_opened_at"] = _now()
                document["workspaces"][index] = item
                document["selected_workspace_id"] = workspace_id
                self._save(document)
                return Workspace.from_dict(item)
        raise WorkspaceError(f"未找到工作区：{workspace_id}")

    def remove(self, workspace_id: str) -> None:
        document = self._load()
        document["workspaces"] = [
            item for item in document["workspaces"]
            if item.get("workspace_id") != workspace_id
        ]
        if document.get("selected_workspace_id") == workspace_id:
            document["selected_workspace_id"] = (
                document["workspaces"][0]["workspace_id"] if document["workspaces"] else None
            )
        self._save(document)


class WorkspaceChatStore:
    def __init__(self, path: Path | str = DEFAULT_CHATS_PATH) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "conversations": {}}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"无法读取对话记录：{exc}") from exc
        if not isinstance(document.get("conversations"), dict):
            raise WorkspaceError("对话记录文件格式错误")
        return document

    def _save(self, document: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再替换，避免程序中断时损坏完整对话历史。
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    def get_messages(self, workspace_id: str) -> list[dict[str, str]]:
        conversation = self._load()["conversations"].get(workspace_id, {})
        messages = conversation.get("messages", [])
        return [
            {"role": str(item["role"]), "content": str(item["content"])}
            for item in messages
            if item.get("role") in {"user", "assistant"} and "content" in item
        ]

    def append(self, workspace_id: str, role: str, content: str, profile_id: str) -> None:
        if role not in {"user", "assistant"}:
            raise WorkspaceError(f"不支持的对话角色：{role}")
        document = self._load()
        conversation = document["conversations"].setdefault(
            workspace_id,
            {"messages": [], "updated_at": _now()},
        )
        conversation["messages"].append(
            {
                "role": role,
                "content": content,
                "profile_id": profile_id,
                "created_at": _now(),
            }
        )
        conversation["updated_at"] = _now()
        self._save(document)

    def get_context_state(self, workspace_id: str) -> dict[str, Any]:
        conversation = self._load()["conversations"].get(workspace_id, {})
        state = conversation.get("context_state", {})
        if not isinstance(state, dict):
            return {"summary_version": 1, "covered_message_count": 0, "summary": {}}
        return {
            "summary_version": int(state.get("summary_version", 1)),
            "covered_message_count": max(int(state.get("covered_message_count", 0)), 0),
            "summary": state.get("summary") if isinstance(state.get("summary"), dict) else {},
            "updated_at": state.get("updated_at"),
            "profile_id": state.get("profile_id"),
            "last_error": state.get("last_error"),
        }

    def save_context_state(self, workspace_id: str, state: Mapping[str, Any]) -> None:
        """保存可替换的压缩快照；原始 messages 不做删除或改写。"""

        document = self._load()
        conversation = document["conversations"].setdefault(
            workspace_id,
            {"messages": [], "updated_at": _now()},
        )
        message_count = len(conversation.get("messages", []))
        covered = int(state.get("covered_message_count", 0))
        if not 0 <= covered <= message_count:
            raise WorkspaceError("压缩快照覆盖的消息数量超出原始历史范围")
        conversation["context_state"] = json.loads(
            json.dumps(dict(state), ensure_ascii=False)
        )
        conversation["updated_at"] = _now()
        self._save(document)

    def record_context_error(self, workspace_id: str, error: str) -> None:
        document = self._load()
        conversation = document["conversations"].setdefault(
            workspace_id,
            {"messages": [], "updated_at": _now()},
        )
        state = conversation.setdefault(
            "context_state",
            {"summary_version": 1, "covered_message_count": 0, "summary": {}},
        )
        state["last_error"] = str(error)[:4000]
        state["last_error_at"] = _now()
        conversation["updated_at"] = _now()
        self._save(document)

    def clear(self, workspace_id: str) -> None:
        document = self._load()
        document["conversations"].pop(workspace_id, None)
        self._save(document)


def build_workspace_context(path: str, max_entries: int = 120) -> str:
    """生成只读的浅层文件清单，避免把整个工程或敏感文件发给模型。"""

    root = Path(path)
    ignored = {
        ".git", ".vs", ".venv", "venv", "env", "langchain_env",
        "Binaries", "DerivedDataCache", "Intermediate", "Saved", "__pycache__",
    }
    entries: list[str] = []
    for child in sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
        if child.name in ignored or child.name.startswith(".env"):
            continue
        entries.append(f"{'[DIR]' if child.is_dir() else '[FILE]'} {child.name}")
        if len(entries) >= max_entries:
            break
        if child.is_dir():
            try:
                grandchildren = sorted(
                    child.iterdir(),
                    key=lambda item: (not item.is_dir(), item.name.casefold()),
                )
            except OSError:
                continue
            for grandchild in grandchildren:
                if grandchild.name in ignored or grandchild.name.startswith(".env"):
                    continue
                entries.append(
                    f"  {'[DIR]' if grandchild.is_dir() else '[FILE]'} "
                    f"{child.name}/{grandchild.name}"
                )
                if len(entries) >= max_entries:
                    break
        if len(entries) >= max_entries:
            break
    return "\n".join(entries) or "(工作区为空)"
