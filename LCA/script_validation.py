"""编码智能体生成脚本的离线安全校验。"""

from __future__ import annotations

import ast
from pathlib import Path


class GeneratedScriptValidationError(ValueError):
    """生成脚本不符合稳定执行约束。"""


_BLOCKED_IMPORTS = {"socket", "subprocess", "shutil", "ctypes"}
_BLOCKED_CALLS = {"eval", "exec", "compile", "__import__"}


def validate_generated_python(file_path: str, content: str) -> None:
    """校验语法和关键约束，不执行生成代码。"""

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        raise GeneratedScriptValidationError(f"Python 语法错误: {exc}") from exc

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BLOCKED_CALLS:
                raise GeneratedScriptValidationError(f"禁止调用 {node.func.id}()")

    blocked = sorted(imported_roots & _BLOCKED_IMPORTS)
    if blocked:
        raise GeneratedScriptValidationError(
            "生成脚本不能自行实现系统或网络操作，请调用固定客户端模块: " + ", ".join(blocked)
        )

    # 地形脚本必须复用经过测试的入口，禁止模型生成另一套协议实现。
    if "terrain" in Path(file_path).stem.lower():
        if not ({"create_terrain", "terrain_client"} & imported_roots):
            raise GeneratedScriptValidationError(
                "地形任务脚本必须导入 create_terrain 或 terrain_client"
            )
