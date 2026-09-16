"""Group-specific corpus materialization for the four ablation conditions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .io_utils import content_hash, load_jsonl


KNOWLEDGE_TYPES = ("resource", "standard", "context")


def source_id(record: dict[str, Any], fallback: str) -> str:
    for key in ("resource_id", "clause_id", "standard_id", "context_id", "task_id", "id"):
        if record.get(key):
            return str(record[key])
    return fallback


def render_record(record: dict[str, Any], knowledge_type: str, fallback: str) -> str:
    identifier = source_id(record, fallback)
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return f"[knowledge_type={knowledge_type}; source_id={identifier}]\n{payload}"


def load_corpus(corpus_root: str | Path) -> dict[str, list[dict[str, Any]]]:
    root = Path(corpus_root)
    resource_records: list[dict[str, Any]] = []
    for path in sorted((root / "resources").glob("*.jsonl")):
        resource_records.extend(
            record for record in load_jsonl(path)
            if record.get("index_admission", True)
        )
    return {
        "resource": resource_records,
        "standard": load_jsonl(root / "standards" / "standards.jsonl"),
        # Runtime conversation snapshots are supplied by the runner. This optional
        # file is reserved for fixed benchmark context records.
        "context": load_jsonl(root / "context" / "context.jsonl")
        if (root / "context" / "context.jsonl").exists()
        else [],
    }


def _make_chunk(index_name: str, ordinal: int, text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    digest = content_hash({"index": index_name, "ordinal": ordinal, "text": text})[:16]
    return {
        "chunk_id": f"{index_name}-{ordinal:05d}-{digest}",
        "index_name": index_name,
        "text": text,
        "metadata": metadata,
    }


def uniform_chunks(
    records_by_type: dict[str, list[dict[str, Any]]],
    index_name: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict[str, Any]]:
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("Uniform chunking requires chunk_size > overlap >= 0")

    pieces: list[str] = []
    spans: list[tuple[int, int, str, str]] = []
    cursor = 0
    for knowledge_type in KNOWLEDGE_TYPES:
        for ordinal, record in enumerate(records_by_type.get(knowledge_type, [])):
            text = render_record(record, knowledge_type, f"{knowledge_type}-{ordinal}")
            if pieces:
                pieces.append("\n\n")
                cursor += 2
            start = cursor
            pieces.append(text)
            cursor += len(text)
            spans.append((start, cursor, knowledge_type, source_id(record, f"{knowledge_type}-{ordinal}")))

    stream = "".join(pieces)
    if not stream:
        return []

    chunks: list[dict[str, Any]] = []
    step = chunk_size - chunk_overlap
    for ordinal, start in enumerate(range(0, len(stream), step)):
        end = min(start + chunk_size, len(stream))
        text = stream[start:end]
        overlapping = [span for span in spans if span[0] < end and span[1] > start]
        metadata = {
            "chunking": "uniform_character_window",
            "knowledge_types": sorted({span[2] for span in overlapping}),
            "source_ids": [span[3] for span in overlapping],
            "char_start": start,
            "char_end": end,
        }
        chunks.append(_make_chunk(index_name, ordinal, text, metadata))
        if end == len(stream):
            break
    return chunks


def differentiated_chunks(
    records_by_type: dict[str, list[dict[str, Any]]], index_name: str
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    knowledge_type = index_name
    records = records_by_type.get(knowledge_type, [])

    if knowledge_type == "standard":
        expanded: list[dict[str, Any]] = []
        for record in records:
            clauses = record.get("clauses")
            if isinstance(clauses, list) and clauses:
                for clause in clauses:
                    expanded.append({
                        "knowledge_type": "standard",
                        "standard_id": record.get("standard_id"),
                        "standard_title": record.get("title"),
                        **clause,
                    })
            else:
                expanded.append(record)
        records = expanded

    mode = {
        "resource": "resource_entity",
        "standard": "standard_clause_with_hierarchy",
        "context": "conversation_state_snapshot",
    }[knowledge_type]
    for ordinal, record in enumerate(records):
        identifier = source_id(record, f"{knowledge_type}-{ordinal}")
        text = render_record(record, knowledge_type, identifier)
        chunks.append(_make_chunk(index_name, ordinal, text, {
            "chunking": mode,
            "knowledge_types": [knowledge_type],
            "source_ids": [identifier],
        }))
    return chunks


def build_group_chunks(config: dict[str, Any], corpus: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    group = config["group"]
    if group == "G0":
        return {}

    uniform = config["uniform_chunking"]
    if group == "G1":
        return {"mixed": uniform_chunks(corpus, "mixed", uniform["chunk_size"], uniform["chunk_overlap"])}
    if group == "G2":
        return {
            kind: uniform_chunks({kind: corpus.get(kind, [])}, kind, uniform["chunk_size"], uniform["chunk_overlap"])
            for kind in KNOWLEDGE_TYPES
        }
    if group == "G3":
        return {kind: differentiated_chunks(corpus, kind) for kind in KNOWLEDGE_TYPES}
    raise ValueError(f"Unsupported group: {group}")


def runtime_context_record(
    task: dict[str, Any],
    scene_plan: dict[str, Any],
    decision_plan: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one self-contained, retrievable snapshot after a completed turn."""
    return {
        "context_id": f"{task.get('conversation_id')}::turn-{task.get('turn_id')}",
        "knowledge_type": "context",
        "conversation_id": task.get("conversation_id"),
        "turn_id": task.get("turn_id"),
        "user_prompt": task.get("user_prompt"),
        "scene_plan_snapshot": scene_plan,
        "object_index": [
            {
                "object_id": item.get("object_id"),
                "object_type": item.get("object_type"),
                "operation": item.get("operation"),
            }
            for item in scene_plan.get("objects", [])
        ],
        "change_set": (decision_plan or {}).get("change_set", {}),
        "explicit_constraints": (decision_plan or {}).get("explicit_constraints", []),
        "scene_plan_valid": (validation or {}).get("valid"),
    }


def chunks_for_runtime_context(config: dict[str, Any], records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    records_list = list(records)
    if not records_list or config["group"] == "G0":
        return []
    if config["group"] == "G3":
        return differentiated_chunks({"context": records_list}, "context")
    index_name = "mixed" if config["group"] == "G1" else "context"
    return uniform_chunks(
        {"context": records_list},
        index_name,
        config["uniform_chunking"]["chunk_size"],
        config["uniform_chunking"]["chunk_overlap"],
    )
