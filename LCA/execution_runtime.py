"""生成脚本的受控写入、校验与执行运行时。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from script_validation import validate_generated_python


APP_DIR = Path(__file__).resolve().parent


class ExecutionRuntimeError(RuntimeError):
    """生成脚本不满足执行边界或运行失败。"""


class ExecutionRuntime:
    def __init__(
        self,
        *,
        scripts_dir: Path | str = APP_DIR,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.scripts_dir = Path(scripts_dir).resolve()
        self.timeout_seconds = timeout_seconds
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def resolve_script(self, filename: str) -> Path:
        candidate_name = Path(filename).name
        if candidate_name != filename and str(Path(filename)) != candidate_name:
            raise ExecutionRuntimeError("生成脚本只能使用文件名，不能包含目录")
        if not candidate_name.startswith("generated_") or not candidate_name.endswith(".py"):
            raise ExecutionRuntimeError("生成脚本必须命名为 generated_*.py")
        resolved = (self.scripts_dir / candidate_name).resolve()
        if resolved.parent != self.scripts_dir:
            raise ExecutionRuntimeError("生成脚本路径越界")
        return resolved

    def validate_script(self, filename: str, content: str | None = None) -> dict[str, Any]:
        path = self.resolve_script(filename)
        source = content if content is not None else path.read_text(encoding="utf-8")
        validate_generated_python(str(path), source)
        return {"status": "valid", "path": str(path), "bytes": len(source.encode("utf-8"))}

    def write_generated_script(self, filename: str, content: str) -> dict[str, Any]:
        path = self.resolve_script(filename)
        if not isinstance(content, str) or not content.strip():
            raise ExecutionRuntimeError("生成脚本内容不能为空")
        # 模型有时把 JSON 字符串中的换行原样输出为反斜杠 n，仅在整段没有真实换行时修复。
        if "\\n" in content and "\n" not in content:
            content = content.replace("\\n", "\n")
        validation = self.validate_script(filename, content)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.scripts_dir,
                delete=False,
                suffix=".tmp",
            ) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
        return {"status": "written", **validation}

    @staticmethod
    def _validate_argv(argv: Sequence[str]) -> list[str]:
        if len(argv) > 128:
            raise ExecutionRuntimeError("脚本参数过多")
        clean: list[str] = []
        for value in argv:
            if not isinstance(value, str):
                raise ExecutionRuntimeError("脚本参数必须全部为字符串")
            if "\x00" in value:
                raise ExecutionRuntimeError("脚本参数不能包含空字符")
            clean.append(value)
        return clean

    def execute_generated_script(
        self,
        filename: str,
        argv: Sequence[str] | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        path = self.resolve_script(filename)
        if not path.is_file():
            raise ExecutionRuntimeError(f"生成脚本不存在：{path}")
        self.validate_script(filename)
        clean_argv = self._validate_argv(argv or [])
        if dry_run and "--dry-run" not in clean_argv:
            clean_argv.append("--dry-run")
        command = [sys.executable, str(path), *clean_argv]
        try:
            completed = subprocess.run(
                command,
                cwd=self.scripts_dir,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionRuntimeError(f"脚本执行超时：{self.timeout_seconds} 秒") from exc
        result = {
            "status": "success" if completed.returncode == 0 else "failed",
            "mode": "dry_run" if dry_run else "live",
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "command": command,
        }
        if completed.returncode != 0:
            raise ExecutionRuntimeError(
                "脚本执行失败：" + (completed.stderr.strip() or completed.stdout.strip())
            )
        return result

