# 外部 JSON 判定器接口约定

本工程只负责生成并静态校验 `scene_plan.json`。后续外部判定器建议只读取以下三类输入，避免接触实验组标签造成偏差：

```json
{
  "task": {},
  "candidate_scene_plan": {},
  "gold_or_rule_profile": {
    "gold_json_path": null,
    "constraint_ids": [],
    "tolerance_profile": "default"
  }
}
```

建议判定器统一返回：

```json
{
  "evaluator_version": "...",
  "task_id": "...",
  "valid_json": true,
  "plugin_contract_valid": true,
  "resource_grounded": true,
  "object_completeness": 1.0,
  "constraint_satisfaction": 1.0,
  "context_consistency": null,
  "spatial_semantic_score": 1.0,
  "overall_correct": true,
  "violations": []
}
```

正式评分前需冻结：判定器版本、各分项定义、容差、总体正确条件和人工仲裁规则。盲评时应将 G0–G3 和重复编号映射为匿名 candidate id；同一判定器必须用于所有组。

