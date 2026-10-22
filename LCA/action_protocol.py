"""决策模型动作协议。

本模块只负责把模型回复转换为唯一、经过 Schema 校验的动作对象，
不负责选择工具、执行工具或判断场景任务是否成功。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "agent"
    / "model_action.schema.json"
)


class ActionProtocolError(RuntimeError):
    """模型动作不满足协议时返回的结构化错误。"""

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
class ModelAction:
    """模型唯一允许输出的动作结构。"""

    action: str
    arguments: dict[str, Any]
    rationale: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "arguments": dict(self.arguments),
        }
        if self.rationale is not None:
            result["rationale"] = self.rationale
        return result


def _strip_single_code_fence(text: str) -> str:
    """兼容单个 JSON 代码围栏，但不接受围栏前后的额外说明。"""

    clean = text.strip()
    if not clean.startswith("```"):
        return clean
    lines = clean.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        raise ActionProtocolError(
            "INVALID_JSON_ENVELOPE",
            "模型动作的 JSON 代码围栏不完整",
        )
    opening = lines[0].strip().casefold()
    if opening not in {"```", "```json"}:
        raise ActionProtocolError(
            "INVALID_JSON_ENVELOPE",
            "模型动作只允许使用 JSON 代码围栏",
        )
    return "\n".join(lines[1:-1]).strip()


class ActionProtocol:
    """严格解析一个 JSON 对象，并按统一 Schema 校验顶层字段。"""

    def __init__(
        self,
        *,
        schema: Mapping[str, Any] | None = None,
        schema_path: Path | str = DEFAULT_SCHEMA_PATH,
    ) -> None:
        if schema is None:
            path = Path(schema_path)
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ActionProtocolError(
                    "ACTION_SCHEMA_UNAVAILABLE",
                    f"无法读取模型动作 Schema：{path}",
                ) from exc
        self.schema = dict(schema)
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)

    @staticmethod
    def _format_validation_error(error: Any) -> tuple[str, dict[str, Any]]:
        path = ".".join(str(item) for item in error.absolute_path)
        details = {
            "path": path,
            "validator": error.validator,
        }
        location = f"（字段 {path}）" if path else ""
        return f"模型动作不满足协议{location}：{error.message}", details

    def parse(self, text: str) -> ModelAction:
        if not isinstance(text, str) or not text.strip():
            raise ActionProtocolError("EMPTY_MODEL_ACTION", "模型动作不能为空")
        clean = _strip_single_code_fence(text)
        try:
            payload = json.loads(clean)
        except json.JSONDecodeError as exc:
            raise ActionProtocolError(
                "INVALID_MODEL_JSON",
                "模型输出必须是唯一且完整的 JSON 对象",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc
        if not isinstance(payload, dict):
            raise ActionProtocolError(
                "MODEL_ACTION_NOT_OBJECT",
                "模型输出的 JSON 顶层必须是对象",
            )
        errors = sorted(
            self.validator.iter_errors(payload),
            key=lambda item: tuple(str(part) for part in item.path),
        )
        if errors:
            message, details = self._format_validation_error(errors[0])
            raise ActionProtocolError("INVALID_MODEL_ACTION", message, details=details)
        rationale = payload.get("rationale")
        return ModelAction(
            action=payload["action"].strip(),
            arguments=dict(payload["arguments"]),
            rationale=rationale.strip() if isinstance(rationale, str) else None,
        )
