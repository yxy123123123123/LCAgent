# LCAgent—UnrealMCP UEBox 风格通信协议（第一版）

## 目标

第一版不在 Unreal Editor 内增加 LCAgent 面板。LCAgent 继续作为独立工作台，
通过本机 UnrealMCP TCP 服务连接 UE。通信消息借鉴 UEBox 的请求信封和能力握手，
但暂时保留现有 TCP 传输，避免为了更换传输层而影响已经跑通的地形、资源扫描链路。

## 请求信封

```json
{
  "ver": "1.0",
  "type": "req",
  "method": "get_capabilities",
  "id": "lca-001",
  "params": {},
  "meta": {
    "lcagent": {
      "contract_version": 1,
      "project_path": "E:/UE_project/LandscapeTool"
    }
  }
}
```

`id` 用于把响应对应到任务；`method` 是 UnrealMCP 注册的命令名称；`params`
是该命令的业务参数；`meta.lcagent.project_path` 是目标工程提示，第一版由
LCAgent 记录，后续可用于多 UE 工程路由。

## 响应信封

成功响应：

```json
{
  "ver": "1.0",
  "type": "res",
  "method": "get_capabilities",
  "id": "lca-001",
  "ok": true,
  "data": {
    "contract_version": 1,
    "protocol_version": "1.0",
    "server": "UnrealMCP",
    "project_name": "LandscapeTool",
    "capabilities": ["get_scene_info", "scan_project_assets", "Landscaping"]
  }
}
```

失败响应：

```json
{
  "ver": "1.0",
  "type": "res",
  "method": "terrain.create",
  "id": "lca-002",
  "ok": false,
  "error": {
    "code": "UE_COMMAND_FAILED",
    "message": "..."
  }
}
```

响应还会保留旧版 `status`、`result` 和 `message` 字段，因此旧版 Python 脚本
不需要立即迁移。

## 连接流程

1. 用户启动 UE，并启用 UnrealMCP 插件。
2. LCAgent 点击“测试 UE 连接”。
3. LCAgent 发送 `get_capabilities` 请求。
4. 插件返回契约版本、工程名和能力列表。
5. 契约版本为 `1` 时，LCAgent 允许使用新协议。
6. 如果插件尚未更新，LCAgent 自动使用旧版 `get_scene_info` 进行只读连接探测，
   但会明确标记为“旧协议”，不会伪装成新协议。

## 当前插件需要的操作

本次新增了只读处理器 `get_capabilities`。C++ 插件仍由用户使用 VS2022 编译，
LCAgent 侧的协议测试不依赖 UE 启动即可执行。

## 后续迁移

- 将 `scan_project_assets`、`Landscaping` 等已有命令逐步切换到 `method/id/data`
  信封；
- 增加长连接和心跳；
- 需要多工程同时打开时，用 `project_path` 做严格目标匹配；
- 最后再评估是否把 TCP 替换为本机 WebSocket。
