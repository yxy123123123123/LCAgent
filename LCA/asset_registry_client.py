"""UnrealMCP 资源扫描客户端。"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from terrain_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_RESPONSE_TIMEOUT,
    PROTOCOL_VERSION,
    TerrainCommandError,
    TerrainTransportError,
    send_mcp_command,
)


class AssetScanValidationError(ValueError):
    """扫描参数或服务端结果格式错误。"""


MAX_ASSET_SCAN_RESPONSE_BYTES = 64 * 1024 * 1024


def build_asset_scan_command(
    package_roots: Sequence[str] | None = None,
    *,
    max_results: int = 10000,
    request_id: str | None = None,
) -> dict[str, Any]:
    roots = list(package_roots or ["/Game"])
    if not roots or any(
        not isinstance(root, str) or not root.strip().startswith("/") for root in roots
    ):
        raise AssetScanValidationError("package_roots 必须是以 / 开头的非空 UE 包路径")
    if not 1 <= int(max_results) <= 50000:
        raise AssetScanValidationError("max_results 必须在 1 到 50000 之间")
    identifier = request_id or f"asset-scan-{uuid.uuid4()}"
    if not isinstance(identifier, str) or not identifier.strip():
        raise AssetScanValidationError("request_id 不能为空")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": identifier,
        "type": "scan_project_assets",
        "params": {
            "package_roots": list(dict.fromkeys(root.strip() for root in roots)),
            "max_results": int(max_results),
        },
    }


def scan_project_assets(
    package_roots: Sequence[str] | None = None,
    *,
    max_results: int = 10000,
    request_id: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
) -> dict[str, Any]:
    command = build_asset_scan_command(
        package_roots,
        max_results=max_results,
        request_id=request_id,
    )
    response = send_mcp_command(
        command,
        host=host,
        port=port,
        connect_timeout=connect_timeout,
        response_timeout=response_timeout,
        max_response_bytes=MAX_ASSET_SCAN_RESPONSE_BYTES,
    )
    if response.get("status") != "success":
        raise TerrainCommandError(response)
    if response.get("request_id") not in {None, command["request_id"]}:
        raise TerrainTransportError("资源扫描响应 request_id 不匹配")
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        raise AssetScanValidationError("资源扫描响应缺少 result.assets 数组")
    for index, asset in enumerate(result["assets"]):
        if not isinstance(asset, dict) or not isinstance(asset.get("object_path"), str):
            raise AssetScanValidationError(f"资源扫描响应第 {index} 项格式错误")
    return result
