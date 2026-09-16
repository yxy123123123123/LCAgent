# 军事仿真知识库来源边界

本库分为两层：

1. `standards.jsonl`：已从官方公开 PDF 抽取并保留章节、页码、URL、版本与 SHA-256 的技术依据；
2. `pending_restricted_sources.jsonl`：论文点名但尚未获得原文或准确版本的军用标准/内部资料，只保存元数据，不进入向量索引。

当前纳入的公开资料包括 OGC CDB、CityGML 和 GeoTIFF。它们支持合成环境数据组织、地理空间语义、场景要素分类、坐标/栅格和资源路径规则，但不能替代或冒充 GJB、中国军用术语和内部战场环境规范。

`source_documents/` 保存官方下载原文；`source_manifest.json` 记录文件签名、页数、哈希和抽取数量；`download_attempts.json` 同时保留成功与失败的下载审计。PDF 文本抽取后仍需抽样人工复核章节边界，正式实验前应冻结来源与版本。

MIL-STD-3022 的官方 ASSIST 元数据已核实，但 DLA 的临时 PDF 链接失效，因此当前没有把全文纳入知识库。CDB Implementation Guidance 1.3 的官方页面存在，但预测 PDF URL 返回 404；后续可合法保存官方 HTML，再按章节抽取。
