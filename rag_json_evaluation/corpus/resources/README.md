# 资源路径库

`envmodels_assets.jsonl` 由 `resource_catalog.py` 对以下目录进行只读扫描生成：

```text
E:\UE_project\LandscapeTool\Content\EnvModels
```

路径映射按 UE Content 挂载规则推导为 `/Game/EnvModels/...`。由于本阶段不启动 UE，`ue_object_path_candidate` 只是候选路径，不能视为已核实的 `actor_path`。记录状态统一为 `filesystem_verified_ue_unverified`。

对于静态识别到的 Blueprint，同时生成 `ue_generated_class_path_candidate=/Game/.../Name.Name_C`；材质仍使用对象路径。`compatible_path_fields` 明确记录该资源只可候选填入 `actor_path` 还是 `material_path`。

全部文件会进入审计清单，但只有 `candidate_for_plugin_path=true` 的记录允许作为插件路径候选。目前静态规则得到582条字段级候选（490条 `material_path`、92条 `actor_path`）；这些仍不是UE已验证路径。纹理、普通网格、地图等记录用于资源检索与说明，不能自动充当 RoadAuto/BuildAuto/RiverAuto 的 Actor 类路径。

旧版 `resources.jsonl` 中5条目录外临时记录予以保留审计，但均设置 `index_admission=false`，不会进入 G1–G3 索引，也不能为编码智能体提供路径证据。

正式实验前应通过 UE Asset Registry 导出至少这些字段并回填：`AssetClassPath`、`PackageName`、`ObjectPath`、Blueprint `GeneratedClass`、可实例化性、插件 handler 和人工中文别名。
