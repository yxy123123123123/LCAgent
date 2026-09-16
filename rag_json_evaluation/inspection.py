"""Read-only readiness audit used before any paid or external call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunking import build_group_chunks, load_corpus
from .config import env_value
from .io_utils import load_jsonl


def inspect_setup(config: dict[str, Any], tasks_path: str | Path) -> dict[str, Any]:
    corpus = load_corpus(config["paths"]["corpus_root"])
    tasks_file = Path(tasks_path)
    tasks = load_jsonl(tasks_file) if tasks_file.exists() else []
    chunks = build_group_chunks(config, corpus)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if config["rag_enabled"] and not corpus["standard"]:
        blockers.append({
            "code": "EMPTY_STANDARDS_CORPUS",
            "owner": "user/domain expert",
            "message": "标准/规则知识库为空，G1-G3 无法检索真实规范约束。",
        })
    unverified_standards = [
        item for item in corpus["standard"]
        if item.get("verification_status") == "downloaded_official_pdf_text_extracted"
    ]
    if unverified_standards:
        warnings.append({
            "code": "STANDARD_EXTRACTION_REQUIRES_SAMPLE_REVIEW",
            "message": f"{len(unverified_standards)} 条公开标准记录来自规则化PDF文本抽取；已做自动噪声检查，但冻结前仍需人工抽样复核章节边界。",
        })
    if not tasks:
        blockers.append({
            "code": "EMPTY_FORMAL_TASK_SET",
            "owner": "user/researcher",
            "message": "正式 tasks.jsonl 为空；示例任务不应作为论文正式样本。",
        })
    unverified = [item for item in corpus["resource"] if item.get("verification_status") not in {"verified", "ue_asset_registry_verified"}]
    if unverified:
        warnings.append({
            "code": "UNVERIFIED_RESOURCE_RECORDS",
            "message": f"{len(unverified)} 条资源尚未完成 UE Asset Registry/人工语义核实；其中磁盘清单只证明文件存在。",
        })
    if not env_value(config["model"]["api_key_env"], "DEEPSEEK_API_KEY"):
        blockers.append({
            "code": "MISSING_LLM_API_KEY",
            "owner": "user",
            "message": "未检测到模型 API Key；离线静态校验可用，但不能运行决策/编码智能体。",
        })
    if config["rag_enabled"] and not env_value(config["embedding"]["api_key_env"], "DASHSCOPE_API_KEY"):
        blockers.append({
            "code": "MISSING_EMBEDDING_API_KEY",
            "owner": "user",
            "message": "未检测到嵌入 API Key；不能构建或查询该 RAG 组索引。",
        })
    warnings.append({
        "code": "EXTERNAL_APIS_NOT_SMOKE_TESTED",
        "message": "按当前改造阶段要求，未调用大模型或嵌入 API；凭据存在不代表模型名、额度、网络和 JSON 输出模式可用。",
    })

    if config["rag_enabled"]:
        manifest = Path(config["paths"]["indexes_root"]) / config["group"] / "manifest.json"
        if not manifest.exists():
            blockers.append({
                "code": "INDEX_NOT_BUILT",
                "owner": "experiment operator",
                "message": f"{config['group']} 索引尚未构建。补齐语料后执行 build-indexes。",
            })

    return {
        "group": config["group"],
        "ready": not blockers,
        "corpus_counts": {key: len(value) for key, value in corpus.items()},
        "materialized_chunk_counts": {key: len(value) for key, value in chunks.items()},
        "task_count": len(tasks),
        "blockers": blockers,
        "warnings": warnings,
        "checks_are_read_only": True,
    }
