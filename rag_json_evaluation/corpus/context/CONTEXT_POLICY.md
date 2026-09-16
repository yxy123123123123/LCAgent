# 用户交互上下文记录库策略

上下文库在实验运行中自动生成，不使用全局共享聊天记录，也不把一个实验组的历史提供给另一组。

每个 `outputs/<run_id>/<group>/repeat-XX/context_memory/` 包含两层：

- `raw/`：逐轮追加完整任务、决策输出、检索记录、ScenePlan 和校验结果，用于审计和错误回放；
- `compacted/`：每个 `conversation_id` 只保留一个最新任务状态，内容包括最新完整 ScenePlan、对象索引、显式约束、change set 和校验状态。

压缩采用确定性状态压缩，不额外调用大模型。下一轮检索只读取 `compacted/` 的最新状态，从而避免对早期重复聊天反复向量化。原始记录不会被压缩层覆盖。

实验隔离规则：

1. 每个组和每次 repeat 独立创建上下文目录；
2. G0 不检索上下文；
3. G1 将压缩上下文作为混合索引的运行时 overlay；
4. G2/G3 仅在路由命中 context 时查询运行时 context overlay；
5. 不将正式测试会话回写到静态 `corpus/context`，避免后续任务看到不该出现的答案。

