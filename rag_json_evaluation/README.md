# RAG 差异化切片离线 JSON 评测链路

本目录是与旧 `LCA/Agent.py` 隔离的论文实验实现。这样不会破坏用户现有工程和旧向量库。它保留三类智能体，并固定主链路为：

```text
用户任务
  -> 决策智能体（任务分解 + retrieval_query）
  -> 检索智能体（按 G0/G1/G2/G3 检索）
  -> 编码智能体（生成 ScenePlan JSON）
  -> 离线静态校验
  -> 停止，不连接 Unreal Engine
```

编码智能体是实验主链路的必要阶段，不能使用规则模板替代。每次模型调用都是独立请求，不保留隐式聊天历史；多轮状态只有经过 context 知识检索才能进入编码智能体，从而使上下文 RAG 的贡献可测。

## 四个实验组

| 组别 | RAG | 存储 | 切片 | 路由 | 作用 |
|---|---|---|---|---|---|
| G0 | 无 | 无 | 无 | 无 | 无 RAG 基线 |
| G1 | 有 | 资源/标准/上下文混合单索引 | 统一字符窗口 | 无 | 普通混合 RAG |
| G2 | 有 | 三类分索引 | 同一统一字符窗口 | 固定多标签路由 | 隔离“分库+路由”贡献 |
| G3 | 有 | 三类分索引 | 资源实体、标准条款、上下文快照 | 与 G2 相同 | 隔离差异化切片贡献 |

G1、G2 使用相同 `chunk_size=400`、`chunk_overlap=80`；G2、G3 使用同一个 `rules-v1` 路由器。四组共享模型参数、`top_k=3`、余弦相似度、提示词版本和 ScenePlan Schema。当前没有重排序器，所有组统一为“不重排”，避免引入额外变量。

## ScenePlan JSON

完整机器可读规范位于 `schemas/scene_plan.schema.json`，UE 源码提取的命令契约位于 `schemas/ue_plugin_contract.json`。最小示例见 `data/scene_plan.example.json`。

关键约束：

- 顶层 `plan_mode` 固定为 `full_snapshot`；多轮任务必须输出当前轮完整对象快照。
- 坐标系固定为 UE 世界坐标、厘米、左手 Z-up。
- 坐标字段严格使用小写 `x/y/z`。这是插件 C++ 实际读取格式；旧 Python 示例中的大写 `X/Y/Z` 与插件不一致。
- 每个 `commands[]` 元素都是可单独发送给插件的一条原生 JSON 命令。
- `create/modify` 必须包含对象类型所需命令，并以唯一的 `Execute` 结束。
- 所有资源路径必须出现在用户输入或检索证据中，否则校验为 `UNSUPPORTED_RESOURCE_PATH`。
- 插件未提供通用删除命令；`delete` 仅作为离线状态墓碑并产生警告。

## 输入任务格式

正式任务写入 `data/tasks.jsonl`，一行一题：

```json
{"task_id":"T001","category":"resource","conversation_id":null,"turn_id":1,"user_prompt":"...","gold_json_path":null,"constraint_ids":[],"tolerance_profile":"default"}
```

多轮任务使用相同 `conversation_id`，并按 `turn_id=1,2,...` 顺序排列。`data/tasks.example.jsonl` 仅用于说明格式，不是论文正式数据。

语料格式：

- 资源库会读取 `corpus/resources/*.jsonl`。`envmodels_assets.jsonl` 已登记 `E:\UE_project\LandscapeTool\Content\EnvModels` 的4,642个文件、磁盘路径及 `/Game/EnvModels/...` 候选路径；其中582条静态记录具有字段级插件路径候选，其余只参与语义检索。未启动UE前，任何候选类路径都不可视为已验证。旧版5条目录外记录保留审计但不准入索引。
- 标准库 `corpus/standards/standards.jsonl` 当前包含4份官方公开PDF抽取的章节/术语记录；每条记录保留条款ID、层级、页码、来源URL和文件SHA-256。待提供的GJB/内部资料单独登记，不参与索引。
- 上下文库采用原始记录和压缩状态两层结构，运行器每轮自动保存；下一轮只检索最新完整ScenePlan状态。不同组和repeat完全隔离，详见 `corpus/context/CONTEXT_POLICY.md`。

## 输出与可追溯性

输出目录固定为 `outputs/<run_id>/<group>/repeat-XX/<task_id>/`：

- `input.json`：原始题目；
- `decision.json`、`decision_raw.txt`：决策智能体结构化输出和原文；
- `retrieval.json`：查询、路由原因、chunk id、得分和注入文本；
- `scene_plan.json`、`coding_raw.txt`：编码智能体最终 JSON 和原文；
- `validation.json`：Schema 与插件语义校验；
- `trace.json` 或 `error.json`：阶段、token 使用量、失败位置和时间戳。

索引采用可人工检查的 JSONL 向量格式，而非旧 Chroma 库。每组索引清单记录语料哈希、知识版本、切片方式、嵌入模型、chunk 数和向量维度。运行清单另保存实验代码快照哈希；当前工程根目录没有可用的 Git 元数据，因此不能记录 commit id。

API 自动重试固定为 0。失败样本会原样记录，是否重跑由后续明确的实验协议统一决定，避免 SDK 隐式重试改变各组调用次数。

## 命令（目前不要运行正式实验）

以下命令均从工程根目录执行。`inspect` 和 `validate` 不调用 API；`build-indexes` 调用嵌入 API；`run` 调用大模型/嵌入 API，但永远不调用 UE。

```powershell
# 只读检查
.\langchain_env\Scripts\python.exe -m rag_json_evaluation.cli inspect --group G3

# 补齐语料后分别构建索引（会调用嵌入 API）
.\langchain_env\Scripts\python.exe -m rag_json_evaluation.cli build-indexes --group G1
.\langchain_env\Scripts\python.exe -m rag_json_evaluation.cli build-indexes --group G2
.\langchain_env\Scripts\python.exe -m rag_json_evaluation.cli build-indexes --group G3

# 仅校验已有 JSON（无 API、无 UE）
.\langchain_env\Scripts\python.exe -m rag_json_evaluation.cli validate --scene-plan rag_json_evaluation\data\scene_plan.example.json

# 正式运行示意（当前阶段不执行）
.\langchain_env\Scripts\python.exe -m rag_json_evaluation.cli run --group G0 --run-id paper-v1-g0 --repeats 3
```

正式实验前必须先解决 `BLOCKERS.md`。本次改造只运行了本地单元测试和只读检查，没有构建索引、调用 API、启动 UE 或生成正式实验结果。
