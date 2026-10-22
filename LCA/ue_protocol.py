"""LCAgent 与 UnrealMCP 的 UEBox 风格协议适配层。

第一版保留现有 UnrealMCP 的 TCP 传输，借鉴 UEBox 的逻辑信封：

    {"ver":"1.0", "type":"req", "method":"...", "id":"...", "params":{}}

这样可以先完成能力握手、请求关联和结构化错误，再在后续版本替换为
WebSocket 长连接，而不会把模型、状态机和工具实现绑定到具体传输层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
import uuid

from terrain_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_RESPONSE_TIMEOUT,
    PROTOCOL_VERSION,
    TerrainTransportError,
    send_mcp_command,
)


UE_ENVELOPE_VERSION = "1.0"
UE_CONTRACT_VERSION = 1


class UEProtocolError(RuntimeError):
    """UE 协议握手、响应校验或传输失败。"""


class UEContractMismatchError(UEProtocolError):
    """UE 插件声明的契约版本与 LCAgent 不兼容。"""


class UECommandError(UEProtocolError):
    """UE 插件返回了结构化失败响应。"""

    def __init__(self, response: Mapping[str, Any]):
        self.response = dict(response)
        error = response.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            message = error.get("message")
        else:
            code = None
            message = error or response.get("message")
        detail = f"code={code}: " if code else ""
        super().__init__(f"{detail}{message or 'UE command failed'}")


@dataclass(frozen=True)
class UECapabilities:
    """一次 UE 握手得到的只读能力快照。"""

    contract_version: int | None
    protocol_version: str | None
    server: str | None
    project_name: str | None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    legacy_fallback: bool = False

    @property
    def contract_supported(self) -> bool:
        return self.contract_version == UE_CONTRACT_VERSION and not self.legacy_fallback


def build_ue_request(
    method: str,
    params: Mapping[str, Any] | None = None,
    *,
    request_id: str | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """构造 UEBox 风格请求信封。"""

    normalized_method = str(method or "").strip()
    if not normalized_method:
        raise UEProtocolError("UE method 不能为空")

    identifier = str(request_id or f"lca-{uuid.uuid4()}").strip()
    if not identifier:
        raise UEProtocolError("UE request id 不能为空")

    metadata: dict[str, Any] = {"contract_version": UE_CONTRACT_VERSION}
    if project_path:
        metadata["project_path"] = str(project_path)

    return {
        "ver": UE_ENVELOPE_VERSION,
        "type": "req",
        "method": normalized_method,
        "id": identifier,
        "params": dict(params or {}),
        "meta": {"lcagent": metadata},
    }


def _decode_response(
    response: Mapping[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    """同时解析新信封和旧版 status/result 响应。"""

    response_id = response.get("id")
    if response_id is not None and response_id != request_id:
        raise UEProtocolError("UE 响应 id 与请求不匹配")

    if response.get("type") == "res":
        if response.get("ok") is not True:
            raise UECommandError(response)
        data = response.get("data")
        return dict(data) if isinstance(data, Mapping) else {"value": data}

    # 旧 UnrealMCP 插件没有新信封时仍返回 status/result；保留兼容性。
    if response.get("status") != "success":
        raise UECommandError(response)
    result = response.get("result")
    return dict(result) if isinstance(result, Mapping) else {"value": result}


class UEProtocolClient:
    """面向 LCAgent 的 UE 会话客户端。"""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        project_path: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.project_path = project_path
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        command = build_ue_request(
            method,
            params,
            request_id=request_id,
            project_path=self.project_path,
        )
        try:
            response = send_mcp_command(
                command,
                host=self.host,
                port=self.port,
                connect_timeout=self.connect_timeout,
                response_timeout=self.response_timeout,
            )
        except TerrainTransportError as exc:
            raise UEProtocolError(str(exc)) from exc
        return _decode_response(response, request_id=command["id"])

    def handshake(self) -> UECapabilities:
        """优先读取能力契约；旧插件则降级为只读场景探测。"""

        try:
            result = self.request("get_capabilities")
        except UECommandError as exc:
            # 旧版插件会把 type=req 当作未知命令；此时只做兼容健康检查，
            # 不把旧插件伪装成支持新契约的版本。
            message = str(exc).lower()
            if "unknown" not in message and "req" not in message:
                raise
            legacy = self._legacy_scene_probe()
            return UECapabilities(
                contract_version=None,
                protocol_version=None,
                server="UnrealMCP",
                project_name=str(legacy.get("level") or ""),
                capabilities=("get_scene_info",),
                legacy_fallback=True,
            )

        contract_version = result.get("contract_version")
        if contract_version != UE_CONTRACT_VERSION:
            raise UEContractMismatchError(
                f"UE 插件契约版本为 {contract_version!r}，LCAgent 需要 {UE_CONTRACT_VERSION}"
            )
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise UEProtocolError("UE 能力响应缺少 capabilities 数组")
        return UECapabilities(
            contract_version=contract_version,
            protocol_version=str(result.get("protocol_version") or UE_ENVELOPE_VERSION),
            server=str(result.get("server") or "UnrealMCP"),
            project_name=str(result.get("project_name") or ""),
            capabilities=tuple(capabilities),
        )

    def _legacy_scene_probe(self) -> dict[str, Any]:
        request_id = f"legacy-health-{uuid.uuid4()}"
        response = send_mcp_command(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "type": "get_scene_info",
                "params": {},
            },
            host=self.host,
            port=self.port,
            connect_timeout=self.connect_timeout,
            response_timeout=self.response_timeout,
        )
        if response.get("status") != "success":
            raise UECommandError(response)
        result = response.get("result")
        return dict(result) if isinstance(result, Mapping) else {}
