# LCAgent

**AI 驱动的 Unreal Engine 场景生成助手** — 使用大语言模型（DeepSeek）解释自然语言指令，自动在虚幻引擎中创建地形、道路、材质等军事仿真场景。

## 架构概览

```
用户输入（自然语言）
       │
       ▼
┌──────────────────────────────────┐
│       DecisionAgent（决策者）      │
│   任务分解 · 编排调度 · 执行命令     │
└──────────┬──────────┬────────────┘
           │          │
    ┌──────▼──┐  ┌───▼───────────┐
    │ Retrieval│  │ CodingAgent   │
    │  Agent   │  │  （编码者）     │
    │（检索者） │  │  生成Python脚本 │
    └────┬─────┘  └───┬───────────┘
         │              │
    ┌────▼────┐    ┌───▼────────┐
    │ Chroma  │    │ 写入磁盘     │
    │ 向量库   │    │ .py 脚本    │
    └─────────┘    └───┬────────┘
                       │
                  ┌────▼──────────┐
                  │ subprocess.run│
                  │  执行脚本      │
                  └────┬──────────┘
                       │
                  ┌────▼──────────┐
                  │ Unreal Engine │
                  │ TCP :13377    │
                  │ (MCP 协议)    │
                  └───────────────┘
```

### 两种运行模式

| 模式 | 入口文件 | 说明 |
|---|---|---|
| **多智能体**（推荐） | `Agent.py` | DecisionAgent 编排 RetrievalAgent + CodingAgent，适合复杂任务 |
| **单智能体** | `oneAgent.py` | 单一 ReAct Agent，自带 read/write/terminal 工具，适合简单任务和教学调试 |

## 环境要求

- **Windows** 操作系统
- **Python** ≥ 3.10（已经附带在项目的虚拟环境中）
- **Unreal Engine** — 需运行 MCP 服务端（监听 `localhost:13377`）
- 以下 API 服务的有效密钥：
  - [DeepSeek](https://platform.deepseek.com/)（LLM 推理）
  - [阿里云 DashScope](https://dashscope.aliyun.com/)（文本嵌入，1024维）

---

## 使用说明（Windows CMD）

以下所有操作在 **Windows 命令提示符（CMD）** 中进行。

---

### 第一步：打开 CMD，进入项目根目录

按 `Win + R`，输入 `cmd`，回车打开命令提示符，然后执行：

```cmd
cd /d E:\LCAgent
```

此时你在项目根目录，应能看到 `LCA`、`RAG` 文件夹和 `README.md` 等文件。可以用 `dir` 确认：

```cmd
dir
```

预期输出类似：

```
2026/07/01  10:30    <DIR>          LCA
2026/07/01  10:30    <DIR>          RAG
2026/07/01  10:30    <DIR>          langchain_env
2026/07/01  10:30             3,456 README.md
2026/07/01  10:30               256 requirements.txt
2026/07/01  10:30               152 .gitignore
```

---

### 第二步：激活虚拟环境

项目已自带 Python 虚拟环境，无需额外安装 Python。直接激活：

```cmd
langchain_env\Scripts\activate
```

激活成功后，CMD 提示符前会出现 `(langchain_env)` 标识：

```
(langchain_env) E:\LCAgent>
```

> **如果激活失败**，请确保 CMD 当前在 `E:\LCAgent` 目录下。

---

### 第三步：配置 API 密钥

用记事本编辑 `.env` 文件：

```cmd
notepad LCA\.env
```

将文件中的占位符替换为你的真实密钥：

```env
DEEPSEEK_API_KEY=sk-你的deepseek密钥
DASHSCOPE_API_KEY=sk-你的dashscope密钥
```

保存并关闭。

---

### 第四步：构建知识库（首次使用或资源变更后执行）

进入 LCA 目录：

```cmd
cd LCA
```

构建资源路径向量库：

```cmd
python RAG_path.py
```

预期输出：

```
🔍 开始构建向量库...
✅ 构建完成！有效路径分片数：5

============================================================
📋 向量库完整内容列表
============================================================

【条目 1】
ID：0
路径内容：山地地形：E:/UE_project/DEM/CS_01.tif
------------------------------------------------------------

【条目 2】
ID：1
路径内容：盆地地形：E:/UE_project/DEM/CS_02.tif
------------------------------------------------------------

...（更多条目）...

📊 向量库总条目数：5
```

构建对话记忆向量库：

```cmd
python RAG_memory.py
```

预期输出：

```
🔍 开始构建memory_db向量库（保留旧数据）...
✅ 构建完成！新增文档块：3，累计文档块：3
```

> 每次修改 `RAG\资源库.txt` 后，需重新执行 `python RAG_path.py` 刷新向量库。

---

### 第五步：启动智能助手

```cmd
python Agent.py
```

（可选：指定项目目录）

```cmd
python Agent.py E:\LCAgent\LCA
```

启动后进入交互式 REPL：

```
欢迎使用环境搭建助手！
📌 规则说明：
  - 仅当输入包含「检索记忆/查看历史/记忆库」时，才会检索记忆库内容
  - 所有交互都会自动记录到记忆库

👤 你: _
```

---

### 第六步：输入任务

在 `👤 你:` 提示符后输入你想要执行的任务，例如：

```
👤 你: 帮我创建一个山地地形，材质使用M4材质
```

Agent 会逐步骤显示思考过程：

```
🤔 DecisionAgent 思考中...

💭 决策智能体-Thought: 这个任务需要先通过检索智能体获取山地地形的高程数据文件路径和M4材质路径
，然后通过编码智能体编写Python脚本来创建地形。

📞 决策智能体：调用检索智能体，查询：山地地形

🤔 RetrievalAgent 思考中...

💭 检索智能体-Thought: 用户需要查询山地地形相关资源路径

🔧 检索智能体-Action: retrieve_path_tool(山地地形)

📚 检索智能体：正在从路径库检索...

✅ 检索智能体-路径库结果：
【结果1】
山地地形：E:/UE_project/DEM/CS_01.tif

🔍 决策智能体-检索智能体返回：路径检索：...

📞 决策智能体：调用编码智能体，查询：创建地形

🤔 CodingAgent 思考中...
📝 编码智能体：文件写入成功 -> E:\LCAgent\LCA\create_terrain.py

🔧 决策智能体-Action: run_terminal_command(python create_terrain.py --file-paths "E:/UE_project/DEM/CS_01.tif" --material-path "/Game/M4UE5Master1_Inst.M4UE5Master1_Inst")

🔍 Observation：山地地形已经在当前关卡中创建成功

🤖 助手: 完成在当前关卡中创建一个山地类型的地形。
--------------------------------------------------------------------------------
👤 你:_
```

---

### 退出

输入以下任一命令即可退出：

```
👤 你: 退出
```

或 `quit` / `exit`。

---

## 日常使用流程总结

以后每次使用时只需：

```cmd
cd /d E:\LCAgent
langchain_env\Scripts\activate
cd LCA
python Agent.py
```

然后输入你的任务即可。知识库只需在资源文件变更时重建。

---

## 单智能体模式（简化版）

如果只需要一个简单的 ReAct Agent（无需多智能体协作），可以使用：

```cmd
cd /d E:\LCAgent
langchain_env\Scripts\activate
cd LCA
python oneAgent.py
```

运行后会提示输入任务，执行终端命令前会要求人工确认（输入 `Y` 确认，`N` 取消）：

```
请输入任务：在LCA目录下创建一个test.txt文件
是否继续？（Y/N）Y
✅ Final Answer：文件已创建
```

---

## 知识库管理

### 查询知识库（不调用 LLM）

```cmd
cd /d E:\LCAgent
langchain_env\Scripts\activate
cd LCA
python test_path.py
```

运行后输入查询内容，可直接检索路径库和记忆库：

```
请输入查询内容：路径库|山地
```

### 自定义资源

编辑 `RAG\资源库.txt`，按格式添加：

```
资源描述：资源路径
山地地形：E:/UE_project/DEM/dem_mountain.tif
沙漠地形：E:/UE_project/DEM/desert.tif
M4材质：/Game/Materials/M4UE5Master1_Inst.M4UE5Master1_Inst
```

修改后重建：

```cmd
python RAG_path.py
```

---

## Unreal Engine 连接测试

确保 UE 端的 MCP 服务运行在 `localhost:13377`，然后测试连接：

```cmd
cd /d E:\LCAgent
langchain_env\Scripts\activate
cd LCA
python test.py
```

---

## 目录结构

```
E:\LCAgent\
├── LCA\                       # 源代码
│   ├── Agent.py               # 主入口 —— 多智能体系统
│   ├── oneAgent.py            # 简化版单智能体
│   ├── config.py              # 统一配置（API 密钥等）
│   ├── prompt_template.py     # 系统提示词模板
│   ├── one_prompt_template.py # 单智能体提示词模板
│   ├── RAG_path.py            # 资源路径向量库（构建 + 检索）
│   ├── RAG_memory.py          # 对话记忆向量库（构建 + 检索 + 存储）
│   ├── create_terrain.py      # 地形生成脚本（连接 UE）
│   ├── create_road.py         # 道路/河流生成脚本（连接 UE）
│   ├── test.py                # UE 连接测试工具
│   ├── test_path.py           # 知识库查询工具
│   └── .env                   # 环境变量（不纳入版本控制）
├── RAG/                       # RAG 数据
│   ├── 资源库.txt              # 资源名称 → 路径映射
│   ├── path_db/               # Chroma 向量库（资源路径）
│   ├── memory/                # 对话历史
│   │   └── history.txt
│   └── memory_db/             # Chroma 向量库（对话记忆）
├── langchain_env/             # Python 虚拟环境（所有依赖已安装）
├── requirements.txt
├── .gitignore
└── README.md
```

## RAG 知识库

项目使用两套独立的 Chroma 向量数据库：

| 数据库 | 存储内容 | 用途 |
|---|---|---|
| `path_db` | 资源文件路径（地形 .tif、材质 /Game/...） | 根据自然语言查询找到对应的 UE 资源路径 |
| `memory_db` | 历史对话记录 | 检索之前的交互历史，辅助上下文理解 |

---

## Unreal Engine 通信协议

Agent 通过 TCP 连接 UE 端的 MCP 服务（`localhost:13377`），使用 JSON 格式发送指令：

```json
{
  "type": "Landscaping",
  "params": {
    "command_type": "DEM",
    "file_paths": "E:/UE_project/DEM/terrain.tif"
  }
}
```

支持的命令类型：

| 类型 | 子命令 |
|---|---|
| `Landscaping` | `DEM`, `Set_Material`, `Execute` |
| `RoadAuto` | `Actor`, `Paint_Layer`, `Points`, `Execute` |
| `RiverAuto` | `Actor`, `Paint_Layer`, `Points`, `Width`, `Execute` |
| `BuildAuto` | `Actor`, `Paint_Layer`, `Points`, `Execute` |
| `OceanAuto` | `Points`, `Execute` |
| `Materials` | `Paint_Layer`, `Points`, `Execute` |

## 许可证

Internal research project. Not licensed for redistribution.

---

## Streamlit 前端界面

项目已提供一个 Streamlit 前端控制台，可以在浏览器中执行任务、查看日志、修改模型配置、重建 RAG 向量库，并测试 Unreal Engine 连接。

### 启动方式

在 Windows PowerShell 中执行：

```powershell
cd E:\LCAgent
powershell -ExecutionPolicy Bypass -File .\run_frontend.ps1
```

启动成功后，在浏览器打开：

```text
http://localhost:8501
```

### 前端功能

- 查看当前聊天模型和向量模型配置状态
- 修改 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`
- 修改 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`
- 输入自然语言任务并执行 Agent
- 查看 DecisionAgent / RetrievalAgent / CodingAgent 执行日志
- 测试 UE MCP 服务连接
- 重建 `path_db` 和 `memory_db`
- 查看 `RAG/资源库.txt` 和最近对话记忆

### 注意事项

- 真正执行模型调用前，请先在 `LCA\.env` 中填写真实 API Key。
- 如果只更换聊天大模型，通常只需要修改 `LLM_*` 三项。
- 如果更换向量模型，建议重建 `path_db` 和 `memory_db`。
- UE 相关任务需要确保 Unreal Engine 侧 MCP 服务已启动，并监听 `localhost:13377`。
