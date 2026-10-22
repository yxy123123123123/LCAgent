"""无需大模型的统一检索服务。"""

from __future__ import annotations

from typing import Any, Sequence

from knowledge_base import KnowledgeBaseManager
from resource_registry import ResourceRegistry


class RetrievalService:
    """把知识检索和资源查找暴露为结构化工具。"""

    def __init__(
        self,
        knowledge_base: KnowledgeBaseManager,
        resource_registry: ResourceRegistry,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.resource_registry = resource_registry

    def search_knowledge(
        self,
        query: str,
        *,
        embedding_profile_id: str,
        kb_ids: Sequence[str] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        results = self.knowledge_base.search(
            query,
            kb_ids=kb_ids,
            embedding_profile_id=embedding_profile_id,
            top_k=top_k,
        )
        return {
            "tool": "search_knowledge",
            "query": query,
            "count": len(results),
            "results": results,
        }

    def search_assets(
        self,
        query: str,
        *,
        asset_class: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        results = self.resource_registry.search(
            query,
            asset_class=asset_class,
            confirmed_only=True,
            limit=limit,
        )
        return {
            "tool": "search_assets",
            "query": query,
            "confirmed_only": True,
            "count": len(results),
            "results": results,
        }

