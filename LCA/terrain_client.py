"""稳定的 UnrealMCP 地形客户端。

编码智能体生成的任务脚本只调用本模块，不再自行实现 Socket 和 JSON 协议，
从而把容易出错的通信逻辑收敛到一个经过自动测试的固定实现中。
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL_VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 13377
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_RESPONSE_TIMEOUT = 300.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class TerrainClientError(RuntimeError):
    """地形客户端错误基类。"""


class TerrainValidationError(TerrainClientError):
    """发送到 UE 前发现请求参数不合法。"""


class TerrainTransportError(TerrainClientError):
    """UnrealMCP TCP 通信失败。"""


class TerrainCommandError(TerrainClientError):
    """UnrealMCP 返回了结构化失败响应。"""

    def __init__(self, response: Mapping[str, Any]):
        self.response = dict(response)
        message = str(response.get("message") or "Terrain creation failed")
        code = response.get("code")
        stage = response.get("stage")
        details = ", ".join(
            item for item in (
                f"code={code}" if code else "",
                f"stage={stage}" if stage else "",
            )
            if item
        )
        super().__init__(f"{message} ({details})" if details else message)


def _validate_file_paths(file_paths: Sequence[str], require_files: bool) -> list[str]:
    if isinstance(file_paths, (str, bytes)) or not file_paths:
        raise TerrainValidationError("file_paths must be a non-empty list")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise TerrainValidationError("Every terrain file path must be a non-empty string")
        path = Path(raw_path).expanduser()
        if path.suffix.lower() not in {".tif", ".tiff"}:
            raise TerrainValidationError(f"Unsupported terrain file type: {raw_path}")
        if require_files and not path.is_file():
            raise TerrainValidationError(f"Terrain file does not exist: {raw_path}")

        canonical = str(path.resolve(strict=False))
        key = canonical.casefold()
        if key not in seen:
            normalized.append(canonical)
            seen.add(key)

    return normalized


def _validate_material_path(material_path: str) -> str:
    if not isinstance(material_path, str) or not material_path.strip():
        raise TerrainValidationError("material_path must be a non-empty string")
    material_path = material_path.strip()
    if not material_path.startswith("/Game/"):
        raise TerrainValidationError("material_path must be an Unreal object path under /Game/")
    if "\\" in material_path:
        raise TerrainValidationError("material_path must use forward slashes")
    return material_path


def build_terrain_command(
    file_paths: Sequence[str],
    material_path: str,
    *,
    request_id: str | None = None,
    require_files: bool = True,
) -> dict[str, Any]:
    """构造并校验唯一标准格式的地形创建命令。"""

    validated_paths = _validate_file_paths(file_paths, require_files=require_files)
    validated_material = _validate_material_path(material_path)
    validated_request_id = request_id or f"terrain-{uuid.uuid4()}"
    if not isinstance(validated_request_id, str) or not validated_request_id.strip():
        raise TerrainValidationError("request_id must be a non-empty string")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": validated_request_id,
        "type": "Landscaping",
        "params": {
            "command_type": "Create",
            "file_paths": validated_paths,
            "material_path": validated_material,
        },
    }


def send_mcp_command(
    command: Mapping[str, Any],
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """发送一条 JSON 命令，并读取一条完整 JSON 响应。"""

    # 换行是网络帧边界；服务端仍保留对旧客户端“无换行完整 JSON”的兼容。
    payload = (
        json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    deadline = time.monotonic() + response_timeout
    response = bytearray()

    try:
        with socket.create_connection((host, port), timeout=connect_timeout) as sock:
            sock.sendall(payload)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TerrainTransportError("Timed out waiting for UnrealMCP response")
                sock.settimeout(remaining)
                chunk = sock.recv(4096)
                if not chunk:
                    if not response:
                        raise TerrainTransportError("UnrealMCP closed the connection without a response")
                    break
                response.extend(chunk)
                if len(response) > max_response_bytes:
                    raise TerrainTransportError("UnrealMCP response exceeded the configured size limit")

                # TCP 可能把同一条响应拆成多个数据包；只有完整 JSON 才返回。
                try:
                    decoded = json.loads(response.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(decoded, dict):
                    raise TerrainTransportError("UnrealMCP response must be a JSON object")
                return decoded
    except TerrainClientError:
        raise
    except (OSError, TimeoutError) as exc:
        raise TerrainTransportError(f"Unable to communicate with UnrealMCP at {host}:{port}: {exc}") from exc

    try:
        decoded = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TerrainTransportError("UnrealMCP returned incomplete or invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise TerrainTransportError("UnrealMCP response must be a JSON object")
    return decoded


def create_terrain(
    file_paths: Sequence[str],
    material_path: str,
    *,
    request_id: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    require_files: bool = True,
) -> dict[str, Any]:
    """使用一条原子 UnrealMCP 命令创建地形。"""

    command = build_terrain_command(
        file_paths,
        material_path,
        request_id=request_id,
        require_files=require_files,
    )
    response = send_mcp_command(
        command,
        host=host,
        port=port,
        connect_timeout=connect_timeout,
        response_timeout=response_timeout,
    )
    response_request_id = response.get("request_id")
    if response_request_id is not None and response_request_id != command["request_id"]:
        raise TerrainTransportError("UnrealMCP response request_id does not match the request")
    response_protocol_version = response.get("protocol_version")
    if response_protocol_version is not None and response_protocol_version != PROTOCOL_VERSION:
        raise TerrainTransportError("UnrealMCP response protocol_version is not supported")
    if response.get("status") != "success":
        raise TerrainCommandError(response)
    return response
