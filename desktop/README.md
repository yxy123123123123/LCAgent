# LCAgent Desktop

Electron + Vue3 客户端骨架。它不替换 UnrealMCP 的 TCP 通信，桌面端通过
Electron 主进程启动 `LCA/service.py`，再由 Python 服务复用现有模型网关、状态机、
资源注册表和 UE 客户端。

## 开发启动

在 `E:\LCAgent\desktop` 下执行：

```powershell
pnpm install
pnpm dev
```

如果 PowerShell 没有安装 pnpm，可以直接使用 npm：

```powershell
# 如果之前用过 pnpm，先删除 pnpm 生成的依赖链接目录
Remove-Item -Recurse -Force .\node_modules
npm install
npm run dev
```

也可以不做全局安装，使用 npx 临时调用 pnpm：

```powershell
npx --yes pnpm@11.19.0 install
npx --yes pnpm@11.19.0 dev
```

第一次安装需要下载 Electron 运行时；如果之前使用过 `--ignore-scripts`，请重新执行
`pnpm install --force`。

工程的 `.npmrc` 已配置 Electron 镜像。如果下载脚本仍未读取配置，可显式执行：

```powershell
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
node .\node_modules\electron\install.js
```

如果 Python 不在 `langchain_env` 中，可设置：

```powershell
$env:LCAGENT_PYTHON = "C:\Path\to\python.exe"
pnpm dev
```

## 当前功能

- Electron 桌面窗口；
- Vue3 工作区列表和路径打开；
- Python 服务健康检查；
- 模型档案读取；
- 工作区对话接口；
- UEBox 风格的 UnrealMCP 能力握手；
- 连接状态、插件能力和任务时间线占位。

## 暂未实现

- 流式模型输出；
- 资源注册表完整页面；
- 知识库管理页面；
- Electron 正式安装包；
- UnrealMCP TCP 之外的新传输方式。
