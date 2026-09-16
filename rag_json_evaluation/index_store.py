"""Transparent JSONL vector indexes for reproducible offline inspection."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Protocol

from .chunking import build_group_chunks, load_corpus
from .io_utils import content_hash, load_json, load_jsonl, utc_now, write_json_atomic, write_jsonl_atomic


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _batch(values: list[Any], batch_size: int) -> list[list[Any]]:
    return [values[start:start + batch_size] for start in range(0, len(values), batch_size)]


def build_indexes(config: dict[str, Any], embedder: Embedder, batch_size: int = 32) -> dict[str, Any]:
    if config["group"] == "G0":
        return {"group": "G0", "status": "skipped_no_rag", "indexes": {}}

    corpus = load_corpus(config["paths"]["corpus_root"])
    missing_required = [kind for kind in ("resource", "standard") if not corpus[kind]]
    if missing_required:
        raise ValueError(
            "Refusing to build an incomplete RAG index; empty required corpus: "
            + ", ".join(missing_required)
        )
    grouped_chunks = build_group_chunks(config, corpus)
    root = Path(config["paths"]["indexes_root"]) / config["group"]
    root.mkdir(parents=True, exist_ok=True)
    index_summaries: dict[str, Any] = {}

    for index_name, chunks in grouped_chunks.items():
        index_dir = root / index_name
        index_dir.mkdir(parents=True, exist_ok=True)
        vectors: list[list[float]] = []
        for chunk_batch in _batch(chunks, batch_size):
            vectors.extend(embedder.embed([chunk["text"] for chunk in chunk_batch]))
        if len(vectors) != len(chunks):
            raise RuntimeError(f"Embedding count mismatch for {index_name}: {len(vectors)} != {len(chunks)}")

        rows = [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]
        write_jsonl_atomic(index_dir / "index.jsonl", rows)
        summary = {
            "index_name": index_name,
            "chunk_count": len(chunks),
            "content_hash": content_hash(chunks),
            "vector_dimension": len(vectors[0]) if vectors else 0,
        }
        write_json_atomic(index_dir / "manifest.json", summary)
        index_summaries[index_name] = summary

    manifest = {
        "experiment_version": config["experiment_version"],
        "knowledge_version": config["knowledge_version"],
        "group": config["group"],
        "storage_mode": config["storage_mode"],
        "chunking_mode": config["chunking_mode"],
        "embedding_model": embedder.model,
        "created_at": utc_now(),
        "corpus_hash": content_hash(corpus),
        "indexes": index_summaries,
    }
    write_json_atomic(root / "manifest.json", manifest)
    return manifest


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return float("-inf")
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return float("-inf")
    return numerator / (left_norm * right_norm)


class JsonVectorStore:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.root = Path(config["paths"]["indexes_root"]) / config["group"]
        self.manifest = load_json(self.root / "manifest.json")
        self.rows: dict[str, list[dict[str, Any]]] = {}

    def _load_rows(self, index_name: str) -> list[dict[str, Any]]:
        if index_name not in self.rows:
            path = self.root / index_name / "index.jsonl"
            self.rows[index_name] = load_jsonl(path) if path.exists() else []
        return self.rows[index_name]

    def search(
        self,
        query_vector: list[float],
        index_names: list[str],
        top_k: int,
        runtime_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for index_name in index_names:
            candidates.extend(self._load_rows(index_name))
        candidates.extend(runtime_rows or [])
        scored = [
            {key: value for key, value in row.items() if key != "vector"} | {"score": _cosine(query_vector, row["vector"])}
            for row in candidates
        ]
        scored.sort(key=lambda item: (-item["score"], item["chunk_id"]))
        return scored[:top_k]
