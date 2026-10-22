"""LCAgent 单决策模型状态机。

决策模型同时承担规划和编码，但它不能直接访问文件、数据库、Shell 或 UE。
所有副作用都经过显式状态、结构化动作和确定性工具边界。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import click

from action_protocol import ActionProtocol
from execution_runtime import ExecutionRuntime
from knowledge_base import KnowledgeBaseManager
from model_gateway import ModelGateway
from prompt_template import decision_state_machine_prompt
from resource_registry import ResourceRegistry
from retrieval_service import RetrievalService
from tool_registry import ToolEffect, ToolRegistry, ToolSpec


class AgentState(str, Enum):
    ANALYZE = "ANALYZE"
    RETRIEVE = "RETRIEVE"
    GENERATE = "GENERATE"
    VALIDATE = "VALIDATE"
    EXECUTE = "EXECUTE"
    OBSERVE = "OBSERVE"
    REPAIR = "REPAIR"
    FINISH = "FINISH"


class StateMachineError(RuntimeError):
    """状态机动作、格式或步数错误。"""


class AgentCancelledError(StateMachineError):
    """用户请求取消；当前边界之后不得再发起模型或工具动作。"""

    code = "RUN_CANCELLED"


@dataclass(frozen=True)
class TraceStep:
    sequence: int
    state: str
    action: str | None
    status: str
    detail: Any


@dataclass(frozen=True)
class AgentRunResult:
    final_answer: str
    trace: list[TraceStep]
    model_steps: int
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_answer": self.final_answer,
            "trace": [asdict(item) for item in self.trace],
            "model_steps": self.model_steps,
            "dry_run": self.dry_run,
        }


def _workspace_id(path: Path) -> str:
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:16]


class DecisionAgent:
    """唯一使用聊天大模型的智能体。"""

    ALLOWED_ACTIONS = {
        "search_assets",
        "search_knowledge",
        "write_script",
        "execute_script",
        "finish",
    }

    def __init__(
        self,
        project_directory: str,
        model_profile_id: str | None = None,
        *,
        workspace_id: str | None = None,
        embedding_profile_id: str | None = None,
        model_gateway: ModelGateway | None = None,
        retrieval_service: RetrievalService | None = None,
        execution_runtime: ExecutionRuntime | None = None,
        action_protocol: ActionProtocol | None = None,
        tool_registry: ToolRegistry | None = None,
        should_cancel: Callable[[], bool] | None = None,
        trace_callback: Callable[[TraceStep], None] | None = None,
        max_model_steps: int = 12,
        max_repairs: int = 3,
        dry_run: bool = True,
    ) -> None:
        project_path = Path(project_directory).resolve()
        if not project_path.is_dir():
            raise StateMachineError(f"工作区目录不存在：{project_path}")
        if max_model_steps < 1 or max_repairs < 0:
            raise StateMachineError("状态机步数配置无效")
        self.project_directory = project_path
        self.workspace_id = workspace_id or _workspace_id(project_path)
        self.model_profile_id = model_profile_id
        self.embedding_profile_id = embedding_profile_id
        self.model_gateway = model_gateway or ModelGateway()
        if retrieval_service is None:
            knowledge = KnowledgeBaseManager(self.workspace_id)
            resources = ResourceRegistry(self.workspace_id)
            retrieval_service = RetrievalService(knowledge, resources)
        self.retrieval_service = retrieval_service
        self.execution_runtime = execution_runtime or ExecutionRuntime()
        self.action_protocol = action_protocol or ActionProtocol()
        self.should_cancel = should_cancel or (lambda: False)
        self.trace_callback = trace_callback
        self.max_model_steps = max_model_steps
        self.max_repairs = max_repairs
        self.dry_run = dry_run
        self._authorized_asset_paths: set[str] = set()
        # 当前兼容阶段仍允许 execute_script；真正是否连接 UE 由 dry-run/live 运行时决定。
        self._allowed_tool_effects = {
            ToolEffect.READ_ONLY,
            ToolEffect.LOCAL_WRITE,
            ToolEffect.UE_WRITE,
        }
        self.tool_registry = tool_registry or self._build_tool_registry()

    def _runtime_context(self) -> str:
        try:
            bases = self.retrieval_service.knowledge_base.list_bases()
        except Exception as exc:
            bases = [{"error": str(exc)}]
        try:
            resource_stats = self.retrieval_service.resource_registry.stats()
        except Exception as exc:
            resource_stats = {"error": str(exc)}
        context = {
            "workspace_id": self.workspace_id,
            "workspace_path": str(self.project_directory),
            "execution_mode": "dry_run" if self.dry_run else "live",
            "embedding_profile_id": self.embedding_profile_id,
            "knowledge_bases": bases,
            "resource_registry": resource_stats,
            "generated_scripts_directory": str(self.execution_runtime.scripts_dir),
        }
        return json.dumps(context, ensure_ascii=False, indent=2)

    @staticmethod
    def _text_argument(arguments: Mapping[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise StateMachineError(f"动作参数 {name} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _object_output_schema() -> dict[str, Any]:
        return {"type": "object"}

    def _build_tool_registry(self) -> ToolRegistry:
        """集中注册现有动作，使参数、提示词和调度共用同一事实来源。"""

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="search_assets",
                description="从当前工作区已审核的 UE 资源注册表中检索资产路径",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "asset_class": {"type": ["string", "null"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
                output_schema=self._object_output_schema(),
                effect=ToolEffect.READ_ONLY,
                idempotent=True,
                metadata={
                    "trace_state": AgentState.RETRIEVE.value,
                    "trace_argument_fields": ["query", "asset_class", "limit"],
                },
            ),
            self._tool_search_assets,
        )
        registry.register(
            ToolSpec(
                name="search_knowledge",
                description="使用选定的 Embedding 模型检索用户知识库",
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "kb_ids": {
                            "type": ["array", "null"],
                            "items": {"type": "string", "minLength": 1},
                            "uniqueItems": True,
                        },
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "additionalProperties": False,
                },
                output_schema=self._object_output_schema(),
                effect=ToolEffect.READ_ONLY,
                idempotent=True,
                metadata={
                    "trace_state": AgentState.RETRIEVE.value,
                    "trace_argument_fields": ["query", "kb_ids", "top_k"],
                },
            ),
            self._tool_search_knowledge,
        )
        registry.register(
            ToolSpec(
                name="write_script",
                description="校验并原子写入 LCAgent 受控目录中的 generated_*.py 脚本",
                input_schema={
                    "type": "object",
                    "required": ["filename", "content"],
                    "properties": {
                        "filename": {
                            "type": "string",
                            "pattern": r"^generated_[^/\\]+\.py$",
                        },
                        "content": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
                output_schema=self._object_output_schema(),
                effect=ToolEffect.LOCAL_WRITE,
                idempotent=False,
                metadata={
                    "trace_state": AgentState.GENERATE.value,
                    "trace_argument_fields": ["filename"],
                    "post_success_states": [AgentState.VALIDATE.value],
                },
            ),
            self._tool_write_script,
        )
        registry.register(
            ToolSpec(
                name="execute_script",
                description="执行已校验的 generated_*.py；dry-run/live 由运行时授权决定",
                input_schema={
                    "type": "object",
                    "required": ["filename"],
                    "properties": {
                        "filename": {
                            "type": "string",
                            "pattern": r"^generated_[^/\\]+\.py$",
                        },
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 128,
                        },
                    },
                    "additionalProperties": False,
                },
                output_schema=self._object_output_schema(),
                effect=ToolEffect.UE_WRITE,
                idempotent=False,
                metadata={
                    "trace_state": AgentState.EXECUTE.value,
                    "trace_argument_fields": ["filename"],
                },
            ),
            self._tool_execute_script,
        )
        registry.register(
            ToolSpec(
                name="finish",
                description="结束当前模型循环并向用户返回说明；不能替代后续场景目标验证",
                input_schema={
                    "type": "object",
                    "required": ["answer"],
                    "properties": {
                        "answer": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ_ONLY,
                idempotent=True,
                terminal=True,
                metadata={"trace_state": AgentState.FINISH.value},
            )
        )
        return registry

    def _tool_search_assets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        observation = self.retrieval_service.search_assets(
            self._text_argument(arguments, "query"),
            asset_class=arguments.get("asset_class"),
            limit=int(arguments.get("limit", 10)),
        )
        for item in observation.get("results", []):
            if isinstance(item, Mapping) and isinstance(item.get("object_path"), str):
                self._authorized_asset_paths.add(item["object_path"])
        return observation

    def _tool_search_knowledge(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.embedding_profile_id:
            raise StateMachineError("未选择 Embedding 模型，无法检索知识库")
        return self.retrieval_service.search_knowledge(
            self._text_argument(arguments, "query"),
            embedding_profile_id=self.embedding_profile_id,
            kb_ids=arguments.get("kb_ids"),
            top_k=int(arguments.get("top_k", 5)),
        )

    def _tool_write_script(self, arguments: dict[str, Any]) -> dict[str, Any]:
        filename = self._text_argument(arguments, "filename")
        content = self._text_argument(arguments, "content")
        self._verify_asset_paths_in_text(content)
        return self.execution_runtime.write_generated_script(filename, content)

    def _tool_execute_script(self, arguments: dict[str, Any]) -> dict[str, Any]:
        filename = self._text_argument(arguments, "filename")
        argv = list(arguments.get("argv", []))
        self._verify_asset_paths_in_text("\n".join(argv))
        return self.execution_runtime.execute_generated_script(
            filename,
            argv,
            dry_run=self.dry_run,
        )

    def _dispatch(
        self, action_name: str, arguments: dict[str, Any]
    ) -> tuple[list[TraceStep], Any]:
        """执行单一结构化动作，并返回该动作内部的状态轨迹。"""

        registered = self.tool_registry.get(action_name)
        metadata = registered.spec.metadata
        trace_fields = metadata.get("trace_argument_fields", [])
        detail = {key: arguments.get(key) for key in trace_fields if key in arguments}
        detail["effect"] = registered.spec.effect.value
        if action_name == "execute_script":
            detail["dry_run"] = self.dry_run

        steps: list[TraceStep] = [
            TraceStep(
                0,
                str(metadata.get("trace_state") or AgentState.ANALYZE.value),
                action_name,
                "running",
                detail,
            )
        ]
        observation = self.tool_registry.execute(
            action_name,
            arguments,
            allowed_effects=self._allowed_tool_effects,
        )
        for state in metadata.get("post_success_states", []):
            steps.append(TraceStep(0, str(state), action_name, "success", observation))
        steps.append(
            TraceStep(0, AgentState.OBSERVE.value, action_name, "success", observation)
        )
        return steps, observation

    def _verify_asset_paths_in_text(self, text: str) -> None:
        """阻止模型绕过资源注册表，把猜测的 UE 对象路径交给执行层。"""

        candidates = set(
            re.findall(r"/[A-Za-z0-9_./-]+\.[A-Za-z0-9_]+", text)
        )
        unauthorized = sorted(candidates - self._authorized_asset_paths)
        if unauthorized:
            raise StateMachineError(
                "脚本或参数包含未经本次 search_assets 确认的 UE 对象路径："
                + ", ".join(unauthorized)
            )

    def _append_trace(self, trace: list[TraceStep], item: TraceStep) -> None:
        trace.append(item)
        if self.trace_callback is not None:
            self.trace_callback(item)

    def _renumber(self, trace: list[TraceStep], items: Sequence[TraceStep]) -> None:
        for item in items:
            self._append_trace(
                trace,
                TraceStep(
                    sequence=len(trace) + 1,
                    state=item.state,
                    action=item.action,
                    status=item.status,
                    detail=item.detail,
                )
            )

    def _raise_if_cancelled(self) -> None:
        if self.should_cancel():
            raise AgentCancelledError("运行已取消，不再发起新的模型或工具动作")

    def run_with_trace(self, user_input: str) -> AgentRunResult:
        task = user_input.strip()
        if not task:
            raise StateMachineError("任务不能为空")
        system_prompt = decision_state_machine_prompt.format(
            runtime_context=self._runtime_context(),
            tool_catalog=self.tool_registry.render_prompt_catalog(),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        trace: list[TraceStep] = []
        repairs = 0
        # 每次任务重新建立最小权限集合，禁止沿用上一次任务检索过的路径。
        self._authorized_asset_paths.clear()

        for model_step in range(1, self.max_model_steps + 1):
            self._raise_if_cancelled()
            self._append_trace(
                trace,
                TraceStep(
                    len(trace) + 1,
                    AgentState.ANALYZE.value,
                    None,
                    "running",
                    {"model_step": model_step},
                )
            )
            reply = self.model_gateway.chat(messages, profile_id=self.model_profile_id)
            # 模型调用本身不能安全强制终止；返回后先检查取消，再决定是否执行动作。
            self._raise_if_cancelled()
            messages.append({"role": "assistant", "content": reply.content})
            action_name: str | None = None
            try:
                action = self.action_protocol.parse(reply.content)
                action_name = action.action
                registered = self.tool_registry.validate_request(
                    action_name,
                    action.arguments,
                    allowed_effects=self._allowed_tool_effects,
                )
                if registered.spec.terminal:
                    self._raise_if_cancelled()
                    answer = self._text_argument(action.arguments, "answer")
                    self._append_trace(
                        trace,
                        TraceStep(
                            len(trace) + 1,
                            AgentState.FINISH.value,
                            action_name,
                            "success",
                            {"answer": answer},
                        )
                    )
                    return AgentRunResult(answer, trace, model_step, self.dry_run)

                self._raise_if_cancelled()
                substeps, observation = self._dispatch(action_name, action.arguments)
                self._renumber(trace, substeps)
                # 工具返回只作为用户可见 observation 输入，不混入系统指令。
                messages.append(
                    {
                        "role": "user",
                        "content": "TOOL_OBSERVATION\n"
                        + json.dumps(observation, ensure_ascii=False),
                    }
                )
            except AgentCancelledError:
                raise
            except Exception as exc:
                repairs += 1
                detail = {"error_type": type(exc).__name__, "message": str(exc)}
                error_code = getattr(exc, "code", None)
                if error_code:
                    detail["code"] = error_code
                error_details = getattr(exc, "details", None)
                if error_details:
                    detail["details"] = error_details
                self._append_trace(
                    trace,
                    TraceStep(
                        len(trace) + 1,
                        AgentState.REPAIR.value,
                        action_name,
                        "failed",
                        detail,
                    )
                )
                messages.append(
                    {
                        "role": "user",
                        "content": "TOOL_ERROR\n" + json.dumps(detail, ensure_ascii=False),
                    }
                )
                if repairs > self.max_repairs:
                    raise StateMachineError(
                        f"状态机修复次数超过上限（{self.max_repairs}）：{exc}"
                    ) from exc

        raise StateMachineError(f"状态机超过最大模型步数：{self.max_model_steps}")

    def run(self, user_input: str) -> str:
        """保持前端旧调用接口，只返回最终答复。"""

        return self.run_with_trace(user_input).final_answer


@click.command()
@click.argument(
    "project_directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=str(Path(__file__).resolve().parent),
)
@click.option("--model-profile-id", default=None)
@click.option("--embedding-profile-id", default=None)
@click.option("--live", is_flag=True, help="允许真实连接 UE；默认只做 dry-run。")
def main(
    project_directory: str,
    model_profile_id: str | None,
    embedding_profile_id: str | None,
    live: bool,
) -> None:
    agent = DecisionAgent(
        project_directory,
        model_profile_id,
        embedding_profile_id=embedding_profile_id,
        dry_run=not live,
    )
    task = input("请输入任务：")
    result = agent.run_with_trace(task)
    print(result.final_answer)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
