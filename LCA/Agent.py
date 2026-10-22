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
from typing import Any, Mapping, Sequence

import click

from execution_runtime import ExecutionRuntime
from knowledge_base import KnowledgeBaseManager
from model_gateway import ModelGateway
from prompt_template import decision_state_machine_prompt
from resource_registry import ResourceRegistry
from retrieval_service import RetrievalService


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


def _extract_json_object(text: str) -> dict[str, Any]:
    """允许代码围栏，但拒绝标签式动作和非对象 JSON。"""

    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    for start, character in enumerate(clean):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(clean[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise StateMachineError("模型输出不是有效的 JSON 对象")


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
        self.max_model_steps = max_model_steps
        self.max_repairs = max_repairs
        self.dry_run = dry_run
        self._authorized_asset_paths: set[str] = set()

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
    def _require_arguments(action: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        action_name = str(action.get("action") or "").strip()
        arguments = action.get("arguments", {})
        if not action_name:
            raise StateMachineError("模型 JSON 缺少 action")
        if not isinstance(arguments, dict):
            raise StateMachineError("模型 JSON 的 arguments 必须是对象")
        return action_name, dict(arguments)

    @staticmethod
    def _text_argument(arguments: Mapping[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise StateMachineError(f"动作参数 {name} 必须是非空字符串")
        return value.strip()

    def _dispatch(
        self, action_name: str, arguments: dict[str, Any]
    ) -> tuple[list[TraceStep], Any]:
        """执行单一结构化动作，并返回该动作内部的状态轨迹。"""

        steps: list[TraceStep] = []
        if action_name == "search_assets":
            query = self._text_argument(arguments, "query")
            limit = int(arguments.get("limit", 10))
            asset_class = arguments.get("asset_class")
            steps.append(
                TraceStep(0, AgentState.RETRIEVE.value, action_name, "running", {"query": query})
            )
            observation = self.retrieval_service.search_assets(
                query,
                asset_class=str(asset_class) if asset_class else None,
                limit=limit,
            )
            for item in observation.get("results", []):
                if isinstance(item, Mapping) and isinstance(item.get("object_path"), str):
                    self._authorized_asset_paths.add(item["object_path"])
        elif action_name == "search_knowledge":
            if not self.embedding_profile_id:
                raise StateMachineError("未选择 Embedding 模型，无法检索知识库")
            query = self._text_argument(arguments, "query")
            top_k = int(arguments.get("top_k", 5))
            kb_ids = arguments.get("kb_ids")
            if kb_ids is not None and not isinstance(kb_ids, list):
                raise StateMachineError("kb_ids 必须是数组或 null")
            steps.append(
                TraceStep(0, AgentState.RETRIEVE.value, action_name, "running", {"query": query})
            )
            observation = self.retrieval_service.search_knowledge(
                query,
                embedding_profile_id=self.embedding_profile_id,
                kb_ids=kb_ids,
                top_k=top_k,
            )
        elif action_name == "write_script":
            filename = self._text_argument(arguments, "filename")
            content = self._text_argument(arguments, "content")
            self._verify_asset_paths_in_text(content)
            steps.append(
                TraceStep(0, AgentState.GENERATE.value, action_name, "running", {"filename": filename})
            )
            # 写入函数内部先做 AST 校验，再原子替换文件。
            observation = self.execution_runtime.write_generated_script(filename, content)
            steps.append(
                TraceStep(0, AgentState.VALIDATE.value, action_name, "success", observation)
            )
        elif action_name == "execute_script":
            filename = self._text_argument(arguments, "filename")
            argv = arguments.get("argv", [])
            if not isinstance(argv, list):
                raise StateMachineError("argv 必须是字符串数组")
            self._verify_asset_paths_in_text("\n".join(str(item) for item in argv))
            steps.append(
                TraceStep(
                    0,
                    AgentState.EXECUTE.value,
                    action_name,
                    "running",
                    {"filename": filename, "dry_run": self.dry_run},
                )
            )
            observation = self.execution_runtime.execute_generated_script(
                filename,
                argv,
                dry_run=self.dry_run,
            )
        else:
            raise StateMachineError(f"不允许的动作：{action_name}")
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

    @staticmethod
    def _renumber(trace: list[TraceStep], items: Sequence[TraceStep]) -> None:
        for item in items:
            trace.append(
                TraceStep(
                    sequence=len(trace) + 1,
                    state=item.state,
                    action=item.action,
                    status=item.status,
                    detail=item.detail,
                )
            )

    def run_with_trace(self, user_input: str) -> AgentRunResult:
        task = user_input.strip()
        if not task:
            raise StateMachineError("任务不能为空")
        system_prompt = decision_state_machine_prompt.format(
            runtime_context=self._runtime_context()
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
            trace.append(
                TraceStep(
                    len(trace) + 1,
                    AgentState.ANALYZE.value,
                    None,
                    "running",
                    {"model_step": model_step},
                )
            )
            reply = self.model_gateway.chat(messages, profile_id=self.model_profile_id)
            messages.append({"role": "assistant", "content": reply.content})
            action: dict[str, Any] | None = None
            try:
                action = _extract_json_object(reply.content)
                action_name, arguments = self._require_arguments(action)
                if action_name not in self.ALLOWED_ACTIONS:
                    raise StateMachineError(f"不允许的动作：{action_name}")
                if action_name == "finish":
                    answer = self._text_argument(arguments, "answer")
                    trace.append(
                        TraceStep(
                            len(trace) + 1,
                            AgentState.FINISH.value,
                            action_name,
                            "success",
                            {"answer": answer},
                        )
                    )
                    return AgentRunResult(answer, trace, model_step, self.dry_run)

                substeps, observation = self._dispatch(action_name, arguments)
                self._renumber(trace, substeps)
                # 工具返回只作为用户可见 observation 输入，不混入系统指令。
                messages.append(
                    {
                        "role": "user",
                        "content": "TOOL_OBSERVATION\n"
                        + json.dumps(observation, ensure_ascii=False),
                    }
                )
            except Exception as exc:
                repairs += 1
                detail = {"error_type": type(exc).__name__, "message": str(exc)}
                trace.append(
                    TraceStep(
                        len(trace) + 1,
                        AgentState.REPAIR.value,
                        str(action.get("action")) if action else None,
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
