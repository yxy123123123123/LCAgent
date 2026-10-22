# LCAgent 通用验证与修复能力优化方案（v1）

制定日期：2026-10-21

目标工程：`E:\LCAgent`

用途：作为另一开发工程实施 LCAgent 优化时的设计基线。本方案只建设与具体场景无关的通用能力，不冻结道路、建筑、河流等论文实验任务，也不在本阶段引入 LoRA、多模型专家或视觉模型。

## 一、优化目标

本轮优化不是重写 LCAgent，也不是继续增加零散 UE 命令，而是把现有的“模型调用工具并判断工具是否报错”升级为可扩展的通用闭环：

```text
任务输入
  → 结构化任务上下文
  → 注册式工具调用
  → UE 执行
  → 通用场景状态查询
  → 确定性验证
  → 场景差异
  → 受约束修复
  → 再验证与可审计终止
```

改造完成后，具体论文场景只需要增加“任务规范、约束实例和少量领域适配器”，不应再次修改智能体主循环。

## 二、当前工程判断

### 2.1 应保留的基础

- 单一 API 决策模型，检索、校验和执行由确定性工具承担；
- `ANALYZE/RETRIEVE/GENERATE/VALIDATE/EXECUTE/OBSERVE/REPAIR/FINISH` 显式状态轨迹；
- 资源路径必须来自 UE Asset Registry 的授权机制；
- 生成脚本的 AST 校验、受控目录和 dry-run/live 隔离；
- 文档知识库、资源注册表和上下文压缩的分治结构；
- UE 握手、能力发现和旧协议回退的基本框架；
- 已有 Python 单元测试与 Electron 构建链路。

### 2.2 当前主要缺口

1. `Agent.py` 同时承担动作定义、调度、权限、状态机和模型交互，继续加入能力会进一步膨胀。
2. 动作列表在 Python 代码和提示词中重复维护，容易出现接口与提示不一致。
3. 当前 `VALIDATE` 主要验证脚本；`OBSERVE` 主要接收工具返回，尚无任务级场景状态和后置条件验证。
4. `get_scene_info` 只返回 Actor 名称、类型、标签和位置，字段不足，而且存在 Actor 数量截断。
5. `/api/tasks` 为同步调用，没有稳定的 `run_id`、运行持久化、取消、恢复和事件查询。
6. Electron 的任务面板尚未迁移，真实任务主要依赖 Streamlit；同时维护两套新功能会增加成本。
7. 协议文档声称存在 `get_capabilities` 和 `scan_project_assets`，但当前检出的 C++ 源码中没有发现相应处理器，需要核验 UE 实际安装插件与仓库副本是否一致。
8. 当前工作树包含大量修改和未跟踪文件。任何新工程都必须保留这些改动，禁止用 reset、checkout 等方式清理。

## 三、设计原则

1. **增量改造**：旧动作和 `/api/tasks` 暂时兼容，逐步切换，不进行一次性大重构。
2. **确定性判定**：模型可以提出计划和修复方案，但不得自行宣布约束通过。
3. **场景无关**：核心层只认识实体、属性、关系、约束和差异，不硬编码论文案例。
4. **单一事实来源**：动作 Schema、提示词动作说明和运行时校验来自同一工具注册表。
5. **可审计**：每次运行都必须能还原模型输入、动作、工具结果、快照、验证结果和终止原因。
6. **失败显式化**：`not_observable`、协议不支持和返回截断不能被当作 `passed`。
7. **权限最小化**：只读查询、计划生成和有副作用执行分级授权。
8. **兼容优先**：真实 UE 插件能力不足时，应明确降级，不能用猜测数据填充。

## 四、目标架构

```text
Desktop / Streamlit
        │
        ▼
Service API ───── RunStore / ArtifactStore
        │
        ▼
TaskOrchestrator
  ├─ DecisionModel
  ├─ ToolRegistry
  ├─ PermissionPolicy
  ├─ SceneObserver
  ├─ ConstraintEngine
  ├─ SceneDiffer
  └─ RepairController
        │
        ▼
UEProtocolClient ── UnrealMCP Capability/Query/Execute Handlers
```

核心边界：

- `TaskOrchestrator` 决定运行阶段和终止条件；
- `DecisionModel` 只产生结构化动作；
- `ToolRegistry` 定义动作契约并完成调度；
- `SceneObserver` 只负责读取和标准化真实状态；
- `ConstraintEngine` 只负责确定性判定；
- `RepairController` 负责限制修改范围、次数和副作用；
- `RunStore` 保存全过程，不让前端内存成为唯一记录。

## 五、建议新增模块

为降低导入方式和目录重构风险，第一版在现有 `LCA` 目录内增量增加模块：

| 新模块 | 责任 |
|---|---|
| `LCA/tool_registry.py` | 工具注册、输入/输出 Schema、能力要求、副作用等级和调度 |
| `LCA/action_protocol.py` | 模型动作解析、Schema 校验和统一错误类型 |
| `LCA/run_models.py` | `TaskRun`、`RunEvent`、终止原因和运行状态数据类 |
| `LCA/run_store.py` | 运行元数据、事件流和产物索引持久化 |
| `LCA/scene_models.py` | `SceneSnapshot`、`SceneEntity`、查询元数据和版本 |
| `LCA/scene_observer.py` | UE 返回到标准场景快照的适配与完整性检查 |
| `LCA/scene_diff.py` | 带容差的实体、属性和关系变化计算 |
| `LCA/constraint_engine.py` | 约束注册、求值、证据和不可观测状态 |
| `LCA/repair_controller.py` | 修复范围、保护集合、预算和修复后复核 |
| `LCA/task_orchestrator.py` | 新的运行主循环，逐步替代 `Agent.py` 中的编排职责 |

建议新增契约目录：

```text
contracts/
  agent/
    action.schema.json
    tool_observation.schema.json
  scene/
    scene_query.schema.json
    scene_snapshot.schema.json
    scene_diff.schema.json
    constraint_set.schema.json
    validation_report.schema.json
    repair_request.schema.json
  unreal_mcp/
    capabilities.schema.json
    scene_query_request.schema.json
    scene_query_response.schema.json
```

不要立即把全部旧文件移动到 Python package 子目录；先建立稳定接口和测试，之后再决定是否做目录重构。

## 六、分阶段实施计划

## M0：冻结基线与核对真实插件（必须最先完成）

### 工作内容

- 保存当前 `git status`、当前提交和未提交文件清单；
- 在不丢失用户改动的前提下建立可恢复基线，禁止重置工作树；
- 运行现有 Python 测试、Electron typecheck/build；
- 记录 Python、Node、pnpm、UE、UnrealMCP 和操作系统版本；
- 找到 UE 工程实际加载的 UnrealMCP 插件目录；
- 比较实际插件与 `E:\LCAgent\UnrealMCP-master` 的文件或提交差异；
- 实机调用 `get_capabilities`、`get_scene_info`、`scan_project_assets`，保存原始请求和响应。

### 验收标准

- 基线测试结果可重复；
- 明确哪一份 UnrealMCP 源码才是权威版本；
- 协议文档、Python 客户端和实际 C++ Handler 的能力矩阵完成；
- 任何敏感 API Key 不进入日志、Git 或运行产物。

### 禁止事项

- 不执行 `git reset --hard`、`git checkout --` 或批量删除；
- 不在未确认实际插件版本前编写新的 C++ Handler；
- 不以文档中的能力列表代替真实握手结果。

## M1：统一动作契约与工具注册表

### 工作内容

- 定义 `ToolSpec`：名称、描述、输入 Schema、输出 Schema、需要的 UE capability、副作用等级、超时、是否幂等；
- 把 `search_assets`、`search_knowledge`、`write_script`、`execute_script` 注册为首批工具；
- 由注册表生成提示词中的动作说明，删除人工重复清单；
- 在调用工具前校验输入，在返回模型前校验或标准化输出；
- 保留 `DecisionAgent.ALLOWED_ACTIONS` 的兼容层，但不再作为唯一事实来源。

### 验收标准

- 一个动作只需注册一次，提示词、运行时校验和调度同时生效；
- 非法字段、缺失字段、未知动作和能力不足有稳定错误码；
- 现有状态机测试全部通过，并新增工具注册和 Schema 测试；
- 不改变现有 dry-run/live 行为。

## M2：运行生命周期与持久化

### 工作内容

- 定义运行状态：`queued/running/waiting/validating/repairing/succeeded/failed/cancelled`；
- 每次任务生成 `run_id`，每个事件生成连续 `sequence`；
- 保存模型步骤、工具动作、观察、异常、token/费用、时间和终止原因；
- 大型快照和日志作为产物文件保存，事件中只存索引和摘要；
- 新增运行 API：创建、查询状态、查询事件、取消、导出；
- `/api/tasks` 暂时包装新接口，以保持兼容；
- 首版只承诺“持久化和取消”，不要在没有可靠检查点时宣称可从任意步骤恢复。

### 推荐 API

```text
POST /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/events?after_sequence=N
POST /api/runs/{run_id}/cancel
GET  /api/runs/{run_id}/artifacts
```

### 验收标准

- 服务重启后仍能读取已完成运行；
- 同一运行的事件序号严格递增；
- 取消后不再发起新的模型或有副作用工具调用；
- 日志脱敏测试覆盖 API Key、Authorization 和用户配置中的 secret 字段。

## M3：UE 协议 v2 与通用只读场景查询

### 工作内容

- 统一请求信封：`protocol_version/request_id/method/data`；
- 统一响应信封：`request_id/status/result/error/server_metadata`；
- 能力响应增加每项能力的版本、输入 Schema 版本、分页上限和只读/写入属性；
- 新增通用 `query_scene_state`，支持过滤、字段选择、分页和明确截断标记；
- 第一版通用字段包括：实体 ID、名称、标签、类、资源/类路径、transform、world bounds、tags、父子关系和可选组件摘要；
- 旧 `get_scene_info` 保留为降级适配器，但降级结果必须标记缺失字段；
- Python 客户端统一超时、重试策略和结构化错误，不对有副作用命令自动重试。

### 查询设计约束

- 默认只读；
- 可按实体 ID、名称、标签、类型和任务标签过滤；
- 支持 `fields` 投影，避免每次返回完整大场景；
- 返回 `next_cursor`、`total_count`、`returned_count`、`truncated`；
- 对不能取得的字段返回缺失说明，禁止伪造默认值；
- 所有 UE 对象标识必须说明其在一次编辑会话内还是跨会话稳定。

### 验收标准

- 协议契约测试通过；
- 旧插件可明确降级，新插件可按过滤条件分页查询；
- 静态场景连续查询的关键字段一致；
- 大场景不会因 Actor 上限静默漏检；
- C++ Handler 不修改场景，不加载无关资产。

## M4：标准场景快照与差异引擎

### 工作内容

- 把 UE 返回标准化为 `SceneSnapshot`；
- 每个字段保留来源、单位、坐标系和可观测性；
- 生成快照哈希、场景/关卡标识、时间和查询条件；
- 实现带容差的 `SceneDiff`：新增、删除、修改、不可比较；
- 支持保护字段和忽略易变字段；
- 将快照和差异作为运行产物保存。

### 验收标准

- 同一静态场景的两次快照在忽略易变字段后差异为空；
- 人工修改一个 Actor 时只报告对应实体和属性；
- 查询不完整时差异结果标记 `inconclusive`，不得报告“无变化”；
- 容差、单位和坐标系有自动测试。

## M5：通用确定性约束引擎

### 工作内容

- 定义 `ConstraintSpec`、`ConstraintResult` 和 `ValidationReport`；
- 定义求值状态：`passed/failed/not_observable/error/skipped`；
- 建立 `ConstraintEvaluator` 注册机制；
- 首批只实现通用约束族，不绑定具体论文任务：
  - 实体存在和数量；
  - 属性等值、集合包含、数值范围和容差；
  - 实体引用有效性；
  - 关系约束的扩展接口；
- 每个失败结果包含测量值、期望值、证据引用、涉及实体和可建议的修复范围；
- 验证器不得调用聊天模型。

### 验收标准

- 已知正确、错误、字段缺失和查询截断四类测试均有明确结果；
- `not_observable` 不计入通过；
- 新增一种约束只需注册 evaluator，不需要修改主循环；
- 验证报告可完全追溯到某个快照和某个约束集版本。

## M6：任务编排与受约束修复

### 工作内容

- 将主循环逐步迁移到 `TaskOrchestrator`；
- 明确区分：
  - 动作格式错误；
  - 工具/协议执行错误；
  - 场景目标违规；
- 有场景目标的任务在执行后自动查询快照并验证；
- 只有验证报告满足终止策略时才允许标记任务成功；
- 生成 `RepairRequest`，仅向模型提供失败约束、证据、允许修改对象、保护对象和剩余预算；
- 执行修复前后必须生成快照和差异；
- 限制修复次数、模型调用、工具动作、总时间和费用；
- 对无目标规范的普通聊天/检索任务保留原工作流。

### 终止原因必须结构化

```text
goal_satisfied
model_finished_without_verification
constraint_failed
not_observable
tool_error
budget_exhausted
cancelled
protocol_mismatch
```

### 验收标准

- 模型不能通过输出 `finish` 绕过必须执行的场景验证；
- 修复动作越过允许范围时在执行前被拒绝；
- 保护对象发生超容差变化时任务不能被标记为成功；
- 同一验证失败不会形成无上限循环；
- 现有非场景任务仍可运行。

## M7：任务界面与可观测性

### 工作内容

- Electron 增加真正的任务面板：运行状态、当前阶段、事件、预算、取消和导出；
- 显示“脚本校验通过”和“场景约束通过”的区别；
- 展示快照摘要、验证失败和修复前后差异；
- Streamlit 暂时作为管理/调试控制台，不在两套界面同时实现全部新功能；
- 形成运行产物清单和一键导出包，供论文实验复核。

### 验收标准

- UI 刷新或关闭后重新打开，仍可读取运行记录；
- 用户可明确看到 dry-run/live、UE 连接、协议版本和验证状态；
- 所有“成功”状态均能定位到对应验证报告；
- UI 不直接访问 UE，仍通过服务层和权限策略。

## M8：质量门与文档

### 测试层级

1. 单元测试：动作、Schema、快照、差异、约束、修复范围；
2. 契约测试：Python 客户端与 C++ Handler 的请求/响应样例；
3. 集成测试：模型使用假网关、UE 使用假服务器，完整运行闭环；
4. UE smoke test：真实插件、固定小关卡、只读查询和一次受控写入；
5. 回归测试：现有 65 项 Python 测试以及 Electron typecheck/build。

### 必须更新的文档

- 架构说明；
- UE 协议与能力矩阵；
- 运行状态和终止原因；
- 数据 Schema；
- 插件编译、安装与版本核验；
- dry-run/live、安全边界和故障恢复；
- 实验产物目录说明。

## 七、优先级和建议工期

| 优先级 | 里程碑 | 建议时间 | 是否阻塞后续论文任务 |
|---|---|---:|---|
| P0 | M0 基线与插件核对 | 0.5—1 天 | 是 |
| P0 | M1 工具注册与动作契约 | 1—2 天 | 是 |
| P0 | M2 运行持久化最小版 | 1—2 天 | 是 |
| P0 | M3 通用场景查询 | 2—3 天 | 是 |
| P0 | M4 快照与差异 | 1—2 天 | 是 |
| P0 | M5 通用约束引擎 | 2—3 天 | 是 |
| P0 | M6 编排和受约束修复骨架 | 2—3 天 | 是 |
| P1 | M7 Electron 任务面板 | 1—2 天 | 否，可先用 API/测试 |
| P1 | M8 UE 集成测试和文档 | 1—2 天 | 中期展示前必须完成 |

推荐将 P0 分成四个可单独验收的提交批次：

1. `baseline + contracts + tool registry`；
2. `run lifecycle + persistence`；
3. `UE query + snapshot + diff`；
4. `constraint engine + orchestrator + repair guard`。

每一批必须测试通过后再继续，不建议在一个大提交中同时修改 C++、Python、服务和前端。

## 八、暂不实施的内容

- 不增加第二个大模型智能体；
- 不接入 LoRA 或本地专家模型；
- 不引入 VLM 作为主要验证器；
- 不构建完整通用知识图谱或场景图数据库；
- 不先设计具体论文任务及其阈值；
- 不大规模重写 Electron 或删除 Streamlit；
- 不承诺任意中断点恢复；
- 不在真实插件未核验前扩展大量 UE 命令；
- 不把 UnrealMCP 返回 `success` 等同于任务成功。

## 九、完成定义

只有满足以下条件，才能认为“LCAgent 通用优化阶段”完成，可以进入论文具体场景设计：

1. 工具动作存在单一 Schema 来源；
2. 每次任务有持久化 `run_id` 和完整事件链；
3. UE 支持带分页和字段完整性说明的通用只读查询；
4. 场景快照和差异可复现；
5. 通用约束引擎能区分通过、失败和不可观测；
6. 主循环能在执行后自动验证，并依据结构化失败触发受约束修复；
7. 模型无法绕过验证直接宣称成功；
8. 所有既有回归测试通过，并有至少一个假 UE 端到端集成测试；
9. 在真实 UE 中完成只读查询和一次通用闭环 smoke test；
10. 文档能够让另一台机器重新安装插件并复现实验。

## 十、交给另一工程的执行提示词

下面内容可以直接作为另一开发工程的首条任务说明：

> 请在 `E:\LCAgent` 中实施 LCAgent 的通用验证与修复能力优化。首先完整阅读 `E:\LoRA\毕业论文\中期答辩_研究调整\00_工作台账\LCAgent通用验证与修复能力优化方案_v1.md`。当前工作树包含大量用户已有修改和未跟踪文件，必须保留，禁止使用 `git reset --hard`、`git checkout --`、批量删除或覆盖用户改动。先只完成 M0：记录基线、运行现有测试和 Electron 检查、核对 UE 实际加载的 UnrealMCP 插件版本、建立协议能力矩阵，并报告发现的问题；未经 M0 验收不要直接开始大规模重构。后续按 M1—M8 逐阶段实施，每阶段先给出拟修改文件和接口，再编码、补测试并验证。核心要求是保持单一 API 决策模型，建立工具注册表、持久化运行、通用场景查询、标准快照、确定性约束验证、场景差异和受约束局部修复；不要加入 LoRA、多智能体、具体论文场景或视觉模型。旧接口应尽量兼容，任何协议字段都必须有 Schema 和测试。

建议另一工程首次只执行 M0 并返回核验结果。确认真实插件、基线测试和工作树状态之后，再决定 M1 的具体修改，能够显著降低返工和误伤已有代码的风险。
