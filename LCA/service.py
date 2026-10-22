"""LCAgent 本地服务。

Electron 客户端通过本机 HTTP 调用这里的接口。服务复用现有的模型网关、
工作区存储、上下文压缩、状态机和 UnrealMCP 客户端，不在桌面端重复实现
业务逻辑。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from asset_registry_client import scan_project_assets  # noqa: E402
from context_compactor import ContextCompactor  # noqa: E402
from embedding_gateway import EmbeddingGateway, EmbeddingProfileStore  # noqa: E402
from goal_compiler import GoalCompiler, GoalCompilerError  # noqa: E402
from goal_models import GoalSpecError  # noqa: E402
from goal_store import GoalStore, GoalStoreError  # noqa: E402
from knowledge_base import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    KnowledgeBaseError,
    KnowledgeBaseManager,
)
from model_gateway import ModelGateway, ModelProfileStore  # noqa: E402
from observation_planner import ObservationPlanError, ObservationPlanner  # noqa: E402
from resource_folder_report import build_folder_summaries  # noqa: E402
from resource_registry import ResourceRegistry  # noqa: E402
from run_manager import RunManager, RunManagerError  # noqa: E402
from run_store import RunStore, RunStoreError  # noqa: E402
from ue_protocol import UEProtocolClient  # noqa: E402
from workspace_store import (  # noqa: E402
    WorkspaceChatStore,
    WorkspaceError,
    WorkspaceStore,
    build_workspace_context,
)


LOGGER = logging.getLogger("lcagent.service")
MAX_REQUEST_BODY_BYTES = 72 * 1024 * 1024


class ServiceError(RuntimeError):
    """可直接返回给桌面客户端的服务错误。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class LCAgentService:
    def __init__(
        self,
        *,
        run_store: RunStore | None = None,
        run_manager: RunManager | None = None,
        goal_data_root: Path | str | None = None,
    ) -> None:
        self.workspace_store = WorkspaceStore()
        self.chat_store = WorkspaceChatStore()
        self.profile_store = ModelProfileStore()
        self.embedding_profile_store = EmbeddingProfileStore()
        self.run_store = run_store or (run_manager.run_store if run_manager else RunStore())
        self.run_manager = run_manager or RunManager(self.run_store)
        self.goal_data_root = Path(goal_data_root or DEFAULT_DATA_ROOT)

    def _select_workspace(self, workspace_id: str):
        if not workspace_id.strip():
            raise ServiceError("workspace_id 不能为空")
        try:
            return self.workspace_store.select(workspace_id)
        except WorkspaceError as exc:
            raise ServiceError(str(exc), 404) from exc

    def _knowledge_manager(self, workspace_id: str) -> KnowledgeBaseManager:
        self._select_workspace(workspace_id)
        return KnowledgeBaseManager(
            workspace_id,
            embedding_gateway=EmbeddingGateway(self.embedding_profile_store),
        )

    def _goal_store(self, workspace_id: str) -> GoalStore:
        self._select_workspace(workspace_id)
        return GoalStore(workspace_id, data_root=self.goal_data_root)

    @staticmethod
    def _workspace_system_messages(workspace) -> list[dict[str, str]]:
        try:
            context = build_workspace_context(workspace.path)
        except OSError as exc:
            context = f"读取工作区清单失败：{exc}"
        return [{
            "role": "system",
            "content": (
                "你是 LCAgent 的工作区对话助手。\n"
                f"当前工作区：{workspace.path}\n"
                "下面是系统读取的浅层文件清单，它只用于定位工程：\n"
                f"{context}\n\n"
                "不要声称已经读取未提供的文件内容，不要声称已修改文件或执行 UE 操作。"
            ),
        }]

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "LCAgent", "version": "0.3.0"}

    def list_workspaces(self) -> dict[str, Any]:
        return {"workspaces": [asdict(item) for item in self.workspace_store.list()]}

    def open_workspace(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ServiceError("工作区路径不能为空")
        name = str(payload.get("name") or "").strip() or None
        try:
            workspace = self.workspace_store.add(path, name=name)
        except WorkspaceError as exc:
            raise ServiceError(str(exc)) from exc
        return {"workspace": asdict(workspace)}

    def remove_workspace(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """仅移除工作区入口，不删除 UE 工程、知识库或历史对话数据。"""

        workspace_id = str(payload.get("workspace_id") or "").strip()
        existing = {item.workspace_id for item in self.workspace_store.list()}
        if workspace_id not in existing:
            raise ServiceError(f"未找到工作区：{workspace_id}", 404)
        self.workspace_store.remove(workspace_id)
        return {
            "removed_workspace_id": workspace_id,
            "workspaces": [asdict(item) for item in self.workspace_store.list()],
        }

    def list_profiles(self) -> dict[str, Any]:
        profiles = []
        for profile in self.profile_store.list_profiles():
            profiles.append({
                "profile_id": profile.profile_id,
                "display_name": profile.display_name,
                "provider": profile.provider,
                "model": profile.model,
                "base_url": profile.base_url,
                "has_secret": self.profile_store.has_secret(profile),
            })
        return {
            "default_profile_id": self.profile_store.default_profile_id(),
            "profiles": profiles,
        }

    def list_embedding_profiles(self) -> dict[str, Any]:
        profiles = [
            {
                "profile_id": profile.profile_id,
                "display_name": profile.display_name,
                "provider": profile.provider,
                "model": profile.model,
                "has_secret": self.embedding_profile_store.has_secret(profile),
            }
            for profile in self.embedding_profile_store.list_profiles()
        ]
        return {
            "default_profile_id": self.embedding_profile_store.default_profile_id(),
            "profiles": profiles,
        }

    def get_messages(self, workspace_id: str) -> dict[str, Any]:
        return {"messages": self.chat_store.get_messages(workspace_id)}

    def clear_messages(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        self._select_workspace(workspace_id)
        self.chat_store.clear(workspace_id)
        return {"workspace_id": workspace_id, "messages": []}

    def compact_context(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        profile_id = str(payload.get("profile_id") or "").strip() or None
        workspace = self._select_workspace(workspace_id)
        profile = self.profile_store.get(profile_id)
        messages = self.chat_store.get_messages(workspace_id)
        prepared = ContextCompactor(
            ModelGateway(self.profile_store),
            self.chat_store,
        ).prepare(
            workspace_id,
            self._workspace_system_messages(workspace),
            messages,
            profile,
            force=True,
        )
        state = self.chat_store.get_context_state(workspace_id)
        return {
            "workspace_id": workspace_id,
            "status": prepared.status,
            "compressed_now": prepared.compressed_now,
            "covered_message_count": prepared.covered_message_count,
            "estimated_tokens": prepared.estimated_tokens,
            "trigger_tokens": prepared.trigger_tokens,
            "context_window_tokens": prepared.context_window_tokens,
            "compression_error": prepared.compression_error,
            "summary": state.get("summary", {}),
        }

    def chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        profile_id = str(payload.get("profile_id") or "").strip() or None
        content = str(payload.get("content") or "").strip()
        if not workspace_id or not content:
            raise ServiceError("workspace_id 和 content 不能为空")

        workspace = self._select_workspace(workspace_id)

        profile = self.profile_store.get(profile_id)
        gateway = ModelGateway(self.profile_store)
        self.chat_store.append(workspace_id, "user", content, profile.profile_id)
        # 重新读取，确保本轮用户消息进入发送给模型的上下文。
        messages = self.chat_store.get_messages(workspace_id)
        compactor = ContextCompactor(gateway, self.chat_store)
        prepared = compactor.prepare(
            workspace_id,
            self._workspace_system_messages(workspace),
            messages,
            profile,
        )
        reply = gateway.chat(prepared.messages, profile_id=profile.profile_id)
        self.chat_store.append(workspace_id, "assistant", reply.content, profile.profile_id)
        return {
            "reply": reply.content,
            "profile_id": reply.profile_id,
            "model": reply.model,
            "usage": dict(reply.usage),
            "compressed_now": prepared.compressed_now,
            "compression_error": prepared.compression_error,
        }

    def _submit_run(self, payload: Mapping[str, Any]):
        workspace_id = str(payload.get("workspace_id") or "").strip()
        profile_id = str(payload.get("profile_id") or "").strip() or None
        task_text = str(payload.get("task") or "").strip()
        if not workspace_id or not task_text:
            raise ServiceError("workspace_id 和 task 不能为空")
        workspace = self._select_workspace(workspace_id)
        raw_budgets = payload.get("budgets")
        if raw_budgets is not None and not isinstance(raw_budgets, Mapping):
            raise ServiceError("budgets 必须是对象")
        try:
            return self.run_manager.submit(
                workspace_id=workspace.workspace_id,
                workspace_path=workspace.path,
                task_text=task_text,
                model_profile_id=profile_id,
                embedding_profile_id=(
                    str(payload.get("embedding_profile_id") or "").strip() or None
                ),
                dry_run=bool(payload.get("dry_run", True)),
                budgets=raw_budgets,
            )
        except (RunManagerError, ValueError) as exc:
            raise ServiceError(str(exc)) from exc

    def create_run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """异步创建运行；调用方使用 run_id 查询进度和结果。"""

        return {"run": self._submit_run(payload).as_dict()}

    def task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """旧同步接口的兼容包装；新客户端应优先使用 /api/runs。"""

        run = self._submit_run(payload)
        try:
            result = self.run_manager.wait(run.run_id)
        except RunManagerError as exc:
            raise ServiceError(str(exc), 500) from exc
        return {"run_id": run.run_id, "result": result}

    def list_runs(self, query: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str((query.get("workspace_id") or [""])[0]).strip() or None
        try:
            limit = int((query.get("limit") or [100])[0])
        except (TypeError, ValueError) as exc:
            raise ServiceError("limit 必须是整数") from exc
        return {
            "runs": [
                item.as_dict()
                for item in self.run_store.list_runs(workspace_id, limit=limit)
            ]
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        try:
            return {"run": self.run_store.get_run(run_id).as_dict()}
        except RunStoreError as exc:
            raise ServiceError(str(exc), 404) from exc

    def get_run_events(self, run_id: str, query: Mapping[str, Any]) -> dict[str, Any]:
        try:
            after_sequence = int((query.get("after_sequence") or [0])[0])
            events = self.run_store.list_events(run_id, after_sequence=after_sequence)
        except (TypeError, ValueError) as exc:
            raise ServiceError("after_sequence 必须是整数") from exc
        except RunStoreError as exc:
            raise ServiceError(str(exc), 404) from exc
        return {"run_id": run_id, "events": [item.as_dict() for item in events]}

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        try:
            run = self.run_manager.cancel(run_id)
        except RunManagerError as exc:
            raise ServiceError(str(exc), 404) from exc
        return {"run": run.as_dict()}

    def get_run_artifacts(self, run_id: str) -> dict[str, Any]:
        try:
            artifacts = self.run_store.list_artifacts(run_id)
        except RunStoreError as exc:
            raise ServiceError(str(exc), 404) from exc
        return {"run_id": run_id, "artifacts": artifacts}

    def create_goal_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """调用决策模型生成草案；结果必须由用户另行批准。"""

        workspace_id = str(payload.get("workspace_id") or "").strip()
        task_text = str(payload.get("task_text") or "").strip()
        profile_id = str(payload.get("profile_id") or "").strip() or None
        store = self._goal_store(workspace_id)
        try:
            result = GoalCompiler(ModelGateway(self.profile_store)).compile(
                task_text,
                profile_id=profile_id,
            )
            saved = store.save_draft(result.goal)
        except (GoalCompilerError, GoalSpecError, GoalStoreError) as exc:
            raise ServiceError(str(exc)) from exc
        response = result.as_dict()
        response["goal"] = saved.as_dict()
        response["draft_hash"] = saved.approval["draft_hash"]
        return response

    def save_goal_draft(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """保存用户编辑后的结构化草案，不调用模型。"""

        workspace_id = str(payload.get("workspace_id") or "").strip()
        raw_goal = payload.get("goal")
        if not isinstance(raw_goal, Mapping):
            raise ServiceError("goal 必须是对象")
        task_text = str(raw_goal.get("task_text") or payload.get("task_text") or "").strip()
        goal_id = str(raw_goal.get("goal_id") or "").strip() or None
        store = self._goal_store(workspace_id)
        try:
            goal = GoalCompiler.compile_document(task_text, raw_goal, goal_id=goal_id)
            saved = store.save_draft(goal)
        except (GoalCompilerError, GoalSpecError, GoalStoreError) as exc:
            raise ServiceError(str(exc)) from exc
        return {"goal": saved.as_dict(), "draft_hash": saved.approval["draft_hash"]}

    def list_goals(self, workspace_id: str) -> dict[str, Any]:
        try:
            goals = self._goal_store(workspace_id).list()
        except (GoalSpecError, GoalStoreError) as exc:
            raise ServiceError(str(exc)) from exc
        return {"workspace_id": workspace_id, "goals": [item.as_dict() for item in goals]}

    def get_goal(self, goal_id: str, query: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str((query.get("workspace_id") or [""])[0]).strip()
        try:
            goal = self._goal_store(workspace_id).get(goal_id)
        except GoalStoreError as exc:
            raise ServiceError(str(exc), 404) from exc
        except GoalSpecError as exc:
            raise ServiceError(str(exc), 409) from exc
        return {"goal": goal.as_dict()}

    def approve_goal(self, goal_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        expected_hash = str(payload.get("expected_draft_hash") or "").strip()
        approved_by = str(payload.get("approved_by") or "user").strip()
        try:
            goal = self._goal_store(workspace_id).approve(
                goal_id,
                approved_by=approved_by,
                expected_draft_hash=expected_hash,
            )
        except (GoalStoreError, GoalSpecError) as exc:
            raise ServiceError(str(exc), 409) from exc
        return {"goal": goal.as_dict()}

    def create_observation_plan(
        self,
        goal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        store = self._goal_store(workspace_id)
        try:
            goal = store.get(goal_id)
            plan = ObservationPlanner().compile(goal)
            raw_fields = payload.get("supported_fields")
            if raw_fields is not None:
                if not isinstance(raw_fields, list):
                    raise ServiceError("supported_fields 必须是数组")
                ObservationPlanner.validate_capabilities(
                    plan,
                    supported_fields=[str(item) for item in raw_fields],
                    supports_pagination=bool(payload.get("supports_pagination", False)),
                )
            store.save_observation_plan(plan)
        except (GoalStoreError, GoalSpecError, ObservationPlanError) as exc:
            status = (
                409
                if isinstance(exc, (GoalSpecError, ObservationPlanError))
                else 400
            )
            raise ServiceError(str(exc), status) from exc
        return {"observation_plan": plan.as_dict()}

    def ue_handshake(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        host = str(payload.get("host") or "127.0.0.1")
        port = int(payload.get("port") or 13377)
        project_path = str(payload.get("project_path") or "") or None
        info = UEProtocolClient(host=host, port=port, project_path=project_path).handshake()
        return {
            "contract_version": info.contract_version,
            "protocol_version": info.protocol_version,
            "server": info.server,
            "project_name": info.project_name,
            "capabilities": list(info.capabilities),
            "legacy_fallback": info.legacy_fallback,
            "contract_supported": info.contract_supported,
        }

    def scan_ue_assets(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        self._select_workspace(workspace_id)
        raw_roots = payload.get("package_roots") or ["/Game"]
        if not isinstance(raw_roots, list):
            raise ServiceError("package_roots 必须是数组")
        roots = [str(item).strip() for item in raw_roots if str(item).strip()]
        max_results = int(payload.get("max_results") or 10000)
        result = scan_project_assets(
            roots,
            max_results=max_results,
            host=str(payload.get("host") or "127.0.0.1"),
            port=int(payload.get("port") or 13377),
        )
        assets = result.get("assets", [])
        registry = ResourceRegistry(workspace_id)
        ingest = registry.ingest_scan(
            assets,
            scan_revision=str(result.get("scan_revision") or "").strip() or None,
        )
        folders = build_folder_summaries(assets)
        return {
            "workspace_id": workspace_id,
            "scan": {key: value for key, value in result.items() if key != "assets"},
            "ingest": ingest,
            "asset_count": len(assets),
            "folder_count": len(folders),
            "folders": folders,
            "assets": assets,
        }

    def resource_overview(self, workspace_id: str) -> dict[str, Any]:
        self._select_workspace(workspace_id)
        assets = ResourceRegistry(workspace_id).list_resources(include_missing=False)
        folders = build_folder_summaries(assets)
        return {
            "workspace_id": workspace_id,
            "asset_count": len(assets),
            "folder_count": len(folders),
            "folders": folders,
            "assets": assets,
        }

    def knowledge_overview(self, workspace_id: str) -> dict[str, Any]:
        manager = self._knowledge_manager(workspace_id)
        return {
            "workspace_id": workspace_id,
            "knowledge_bases": manager.list_bases(),
            "documents": manager.list_documents(),
        }

    def create_knowledge_base(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        manager = self._knowledge_manager(workspace_id)
        try:
            knowledge_base = manager.create_base(
                str(payload.get("name") or ""),
                str(payload.get("description") or ""),
            )
        except KnowledgeBaseError as exc:
            raise ServiceError(str(exc)) from exc
        return {"knowledge_base": knowledge_base}

    def upload_knowledge_document(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        kb_id = str(payload.get("kb_id") or "").strip()
        filename = str(payload.get("filename") or "").strip()
        embedding_profile_id = str(payload.get("embedding_profile_id") or "").strip()
        encoded = str(payload.get("data_base64") or "").strip()
        if not kb_id or not filename or not embedding_profile_id or not encoded:
            raise ServiceError("知识库、文件名、Embedding 档案和文件内容不能为空")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ServiceError("上传文件的 Base64 内容无效") from exc
        manager = self._knowledge_manager(workspace_id)
        try:
            document = manager.ingest_bytes(
                kb_id,
                filename,
                data,
                embedding_profile_id=embedding_profile_id,
                chunk_size=int(payload.get("chunk_size") or 1000),
                overlap=int(payload.get("overlap") or 150),
            )
        except KnowledgeBaseError as exc:
            raise ServiceError(str(exc)) from exc
        return {"document": document}

    def search_knowledge(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id") or "").strip()
        embedding_profile_id = str(payload.get("embedding_profile_id") or "").strip()
        raw_ids = payload.get("kb_ids") or []
        if not isinstance(raw_ids, list):
            raise ServiceError("kb_ids 必须是数组")
        manager = self._knowledge_manager(workspace_id)
        try:
            matches = manager.search(
                str(payload.get("query") or ""),
                kb_ids=[str(item) for item in raw_ids if str(item).strip()] or None,
                embedding_profile_id=embedding_profile_id,
                top_k=int(payload.get("top_k") or 5),
            )
        except KnowledgeBaseError as exc:
            raise ServiceError(str(exc)) from exc
        return {"matches": matches}

    def dispatch(self, method: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        path = urlparse(str(payload.get("_path") or "")).path
        query = payload.get("_query") if isinstance(payload.get("_query"), Mapping) else {}
        if path == "/api/runs" and method == "GET":
            return self.list_runs(query)
        if path.startswith("/api/runs/"):
            suffix = path[len("/api/runs/") :].strip("/")
            parts = suffix.split("/") if suffix else []
            if not parts or not parts[0]:
                raise ServiceError("run_id 不能为空")
            run_id = parts[0]
            if len(parts) == 1 and method == "GET":
                return self.get_run(run_id)
            if len(parts) == 2 and parts[1] == "events" and method == "GET":
                return self.get_run_events(run_id, query)
            if len(parts) == 2 and parts[1] == "artifacts" and method == "GET":
                return self.get_run_artifacts(run_id)
            if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
                return self.cancel_run(run_id)
            raise ServiceError("接口不存在", 404)
        if path.startswith("/api/goals/") and path not in {
            "/api/goals/draft",
            "/api/goals/save",
        }:
            suffix = path[len("/api/goals/") :].strip("/")
            parts = suffix.split("/") if suffix else []
            if not parts or not parts[0]:
                raise ServiceError("goal_id 不能为空")
            goal_id = parts[0]
            if len(parts) == 1 and method == "GET":
                return self.get_goal(goal_id, query)
            if len(parts) == 2 and parts[1] == "approve" and method == "POST":
                return self.approve_goal(goal_id, payload)
            if len(parts) == 2 and parts[1] == "observation-plan" and method == "POST":
                return self.create_observation_plan(goal_id, payload)
            raise ServiceError("接口不存在", 404)
        if method == "GET" and path.startswith("/api/workspaces/") and path.endswith("/messages"):
            workspace_id = path[len("/api/workspaces/") : -len("/messages")].strip("/")
            if not workspace_id:
                raise ServiceError("workspace_id 不能为空")
            return self.get_messages(workspace_id)
        if method == "GET" and path.startswith("/api/workspaces/") and path.endswith("/resources"):
            workspace_id = path[len("/api/workspaces/") : -len("/resources")].strip("/")
            return self.resource_overview(workspace_id)
        if method == "GET" and path.startswith("/api/workspaces/") and path.endswith("/knowledge"):
            workspace_id = path[len("/api/workspaces/") : -len("/knowledge")].strip("/")
            return self.knowledge_overview(workspace_id)
        if method == "GET" and path.startswith("/api/workspaces/") and path.endswith("/goals"):
            workspace_id = path[len("/api/workspaces/") : -len("/goals")].strip("/")
            return self.list_goals(workspace_id)
        routes = {
            ("GET", "/api/health"): lambda: self.health(),
            ("GET", "/api/workspaces"): lambda: self.list_workspaces(),
            ("POST", "/api/workspaces/open"): lambda: self.open_workspace(payload),
            ("POST", "/api/workspaces/remove"): lambda: self.remove_workspace(payload),
            ("GET", "/api/models"): lambda: self.list_profiles(),
            ("GET", "/api/embedding-models"): lambda: self.list_embedding_profiles(),
            ("POST", "/api/ue/handshake"): lambda: self.ue_handshake(payload),
            ("POST", "/api/ue/assets/scan"): lambda: self.scan_ue_assets(payload),
            ("POST", "/api/chat"): lambda: self.chat(payload),
            ("POST", "/api/chat/clear"): lambda: self.clear_messages(payload),
            ("POST", "/api/context/compact"): lambda: self.compact_context(payload),
            ("POST", "/api/knowledge-bases/create"): lambda: self.create_knowledge_base(payload),
            ("POST", "/api/knowledge-bases/upload"): lambda: self.upload_knowledge_document(payload),
            ("POST", "/api/knowledge-bases/search"): lambda: self.search_knowledge(payload),
            ("POST", "/api/goals/draft"): lambda: self.create_goal_draft(payload),
            ("POST", "/api/goals/save"): lambda: self.save_goal_draft(payload),
            ("POST", "/api/runs"): lambda: self.create_run(payload),
            ("POST", "/api/tasks"): lambda: self.task(payload),
        }
        handler = routes.get((method, path))
        if handler is None:
            raise ServiceError("接口不存在", 404)
        return handler()


class RequestHandler(BaseHTTPRequestHandler):
    service = LCAgentService()

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_REQUEST_BODY_BYTES:
                raise ServiceError("请求体过大", 413)
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(f"请求 JSON 无效：{exc}") from exc
        if not isinstance(value, dict):
            raise ServiceError("请求体必须是 JSON 对象")
        return value

    def _send_json(self, value: Mapping[str, Any], status: int = 200) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _handle(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._read_json() if self.command == "POST" else {}
        payload["_path"] = path
        payload["_query"] = parse_qs(parsed.query, keep_blank_values=False)
        try:
            result = self.service.dispatch(self.command, payload)
            self._send_json({"ok": True, "data": result})
        except ServiceError as exc:
            self._send_json({"ok": False, "error": {"message": str(exc)}}, exc.status)
        except Exception as exc:  # 服务边界统一返回结构化错误，避免进程崩溃。
            LOGGER.exception("请求处理失败")
            self._send_json(
                {"ok": False, "error": {"message": str(exc), "type": type(exc).__name__}},
                500,
            )

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LCAgent 本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[LCAgent] %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    LOGGER.info("服务已启动：http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        # 本地服务退出时释放后台线程池；正在运行的任务会在当前安全边界结束。
        RequestHandler.service.run_manager.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
