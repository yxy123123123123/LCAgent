"""注册式工具契约和调度。

ToolSpec 是动作参数、提示词说明、能力要求和副作用等级的唯一事实来源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator


ToolHandler = Callable[[dict[str, Any]], Any]


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    UE_WRITE = "ue_write"
    EXTERNAL_WRITE = "external_write"


class ToolRegistryError(RuntimeError):
    """工具注册、校验、能力或调度错误。"""

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None
    required_capabilities: tuple[str, ...] = ()
    effect: ToolEffect = ToolEffect.READ_ONLY
    idempotent: bool = False
    default_timeout_ms: int = 10000
    terminal: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("工具名称不能为空")
        if self.default_timeout_ms < 1:
            raise ValueError("工具超时必须大于 0")
        Draft202012Validator.check_schema(dict(self.input_schema))
        if self.output_schema is not None:
            Draft202012Validator.check_schema(dict(self.output_schema))


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler | None


class ToolRegistry:
    """保存工具定义，并在执行前后实施统一契约检查。"""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler | None = None) -> None:
        name = spec.name.strip()
        if name in self._tools:
            raise ToolRegistryError("DUPLICATE_TOOL", f"工具重复注册：{name}")
        if handler is None and not spec.terminal:
            raise ToolRegistryError("TOOL_HANDLER_MISSING", f"工具缺少执行函数：{name}")
        self._tools[name] = RegisteredTool(spec=spec, handler=handler)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolRegistryError("UNKNOWN_TOOL", f"不允许的动作：{name}") from exc

    @staticmethod
    def _first_validation_error(
        schema: Mapping[str, Any], payload: Any
    ) -> Any | None:
        validator = Draft202012Validator(dict(schema))
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.path),
        )
        return errors[0] if errors else None

    @staticmethod
    def _validation_details(error: Any) -> dict[str, Any]:
        return {
            "path": ".".join(str(item) for item in error.absolute_path),
            "validator": error.validator,
        }

    def validate_request(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        available_capabilities: set[str] | None = None,
        allowed_effects: set[ToolEffect] | None = None,
    ) -> RegisteredTool:
        tool = self.get(name)
        error = self._first_validation_error(tool.spec.input_schema, arguments)
        if error is not None:
            raise ToolRegistryError(
                "INVALID_TOOL_ARGUMENTS",
                f"动作 {name} 的参数不满足 Schema：{error.message}",
                details=self._validation_details(error),
            )
        if allowed_effects is not None and tool.spec.effect not in allowed_effects:
            raise ToolRegistryError(
                "TOOL_EFFECT_NOT_ALLOWED",
                f"当前运行策略不允许工具副作用：{tool.spec.effect.value}",
                details={"tool": name, "effect": tool.spec.effect.value},
            )
        if available_capabilities is not None:
            missing = sorted(set(tool.spec.required_capabilities) - available_capabilities)
            if missing:
                raise ToolRegistryError(
                    "TOOL_CAPABILITY_MISSING",
                    f"动作 {name} 缺少所需能力：{', '.join(missing)}",
                    details={"tool": name, "missing_capabilities": missing},
                )
        return tool

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        available_capabilities: set[str] | None = None,
        allowed_effects: set[ToolEffect] | None = None,
    ) -> Any:
        tool = self.validate_request(
            name,
            arguments,
            available_capabilities=available_capabilities,
            allowed_effects=allowed_effects,
        )
        if tool.handler is None:
            raise ToolRegistryError("TERMINAL_TOOL_EXECUTION", f"终止动作不能作为工具执行：{name}")
        result = tool.handler(dict(arguments))
        if tool.spec.output_schema is not None:
            error = self._first_validation_error(tool.spec.output_schema, result)
            if error is not None:
                raise ToolRegistryError(
                    "INVALID_TOOL_OUTPUT",
                    f"动作 {name} 的返回不满足 Schema：{error.message}",
                    details=self._validation_details(error),
                )
        return result

    def render_prompt_catalog(self) -> str:
        """从注册表生成模型可见动作目录，避免在提示词中重复维护参数。"""

        sections: list[str] = []
        for index, registered in enumerate(self._tools.values(), start=1):
            spec = registered.spec
            schema_text = json.dumps(spec.input_schema, ensure_ascii=False, separators=(",", ":"))
            sections.append(
                f"{index}. {spec.name}\n"
                f"   说明：{spec.description}\n"
                f"   副作用：{spec.effect.value}\n"
                f"   arguments Schema：{schema_text}"
            )
        return "\n".join(sections)
