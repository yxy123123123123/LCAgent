"""Retrieval agent implementing G0-G3 with a shared scoring budget."""

from __future__ import annotations

from typing import Any

from .chunking import chunks_for_runtime_context
from .index_store import Embedder, JsonVectorStore
from .routing import route_query


class RetrievalAgent:
    def __init__(self, config: dict[str, Any], embedder: Embedder | None = None) -> None:
        self.config = config
        self.embedder = embedder
        self.store: JsonVectorStore | None = None
        if config["rag_enabled"]:
            if embedder is None:
                raise ValueError("RAG groups require an embedding client")
            self.store = JsonVectorStore(config)

    def retrieve(
        self,
        task: dict[str, Any],
        runtime_context: list[dict[str, Any]] | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        query = str(query or task["user_prompt"])
        if not self.config["rag_enabled"]:
            return {
                "group": self.config["group"],
                "query": query,
                "routes": [],
                "router": {"router_version": "disabled"},
                "results": [],
                "context_text": "",
            }

        assert self.embedder is not None and self.store is not None
        group = self.config["group"]
        if group == "G1":
            route_result = {"routes": ["mixed"], "reasons": {"mixed": ["single_mixed_index"]}, "router_version": "disabled"}
        else:
            route_result = route_query(query, task)

        query_vector = self.embedder.embed([query])[0]
        runtime_chunks = chunks_for_runtime_context(self.config, runtime_context or [])
        runtime_rows: list[dict[str, Any]] = []
        runtime_index_name = "mixed" if group == "G1" else "context"
        if runtime_chunks and runtime_index_name in route_result["routes"]:
            runtime_vectors = self.embedder.embed([chunk["text"] for chunk in runtime_chunks])
            runtime_rows = [{**chunk, "vector": vector} for chunk, vector in zip(runtime_chunks, runtime_vectors)]

        results = self.store.search(
            query_vector=query_vector,
            index_names=route_result["routes"],
            top_k=int(self.config["retrieval"]["top_k"]),
            runtime_rows=runtime_rows,
        )
        for rank, result in enumerate(results, start=1):
            result["rank"] = rank

        limit = int(self.config["retrieval"]["final_context_max_chars"])
        context_blocks: list[str] = []
        used = 0
        for result in results:
            block = f"[chunk_id={result['chunk_id']}; score={result['score']:.6f}]\n{result['text']}"
            if used + len(block) > limit:
                block = block[: max(0, limit - used)]
            if block:
                context_blocks.append(block)
                used += len(block)
            if used >= limit:
                break

        return {
            "group": group,
            "query": query,
            "routes": route_result["routes"],
            "router": route_result,
            "top_k": self.config["retrieval"]["top_k"],
            "results": results,
            "context_text": "\n\n".join(context_blocks),
        }
