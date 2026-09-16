"""Per-run raw conversation logging and deterministic state compression."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .chunking import runtime_context_record
from .io_utils import load_jsonl, utc_now, write_json_atomic, write_jsonl_atomic


class ContextMemoryStore:
    """Keep raw turns and one latest compacted snapshot per conversation.

    The store is rooted inside one group/repeat output directory. It therefore
    cannot leak context between experimental groups or repeats.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_root = self.root / "raw"
        self.compacted_root = self.root / "compacted"
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.compacted_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.root / "manifest.json", {
            "context_store_version": "task-state-v1",
            "created_at": utc_now(),
            "isolation_scope": str(self.root),
            "raw_policy": "append every completed agent turn without truncation",
            "compression_policy": "replace with latest full ScenePlan state plus object index, explicit constraints and change set",
            "llm_used_for_compression": False,
        })

    @staticmethod
    def _key(conversation_id: str) -> str:
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:12]
        safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in conversation_id)
        return f"{safe[:60]}-{digest}"

    def _paths(self, conversation_id: str) -> tuple[Path, Path]:
        key = self._key(conversation_id)
        return self.raw_root / f"{key}.jsonl", self.compacted_root / f"{key}.jsonl"

    def get_compacted(self, conversation_id: str | None) -> list[dict[str, Any]]:
        if not conversation_id:
            return []
        _, compacted_path = self._paths(str(conversation_id))
        return load_jsonl(compacted_path) if compacted_path.exists() else []

    def record_turn(
        self,
        task: dict[str, Any],
        decision: dict[str, Any],
        retrieval: dict[str, Any],
        scene_plan: dict[str, Any],
        validation: dict[str, Any],
    ) -> None:
        conversation_id = task.get("conversation_id")
        if not conversation_id:
            return
        raw_path, compacted_path = self._paths(str(conversation_id))
        raw_records = load_jsonl(raw_path) if raw_path.exists() else []
        raw_records.append({
            "recorded_at": utc_now(),
            "task": task,
            "decision": decision,
            "retrieval": retrieval,
            "scene_plan": scene_plan,
            "validation": validation,
        })
        write_jsonl_atomic(raw_path, raw_records)

        compacted = runtime_context_record(task, scene_plan, decision, validation)
        compacted.update({
            "turn_start": 1,
            "turn_end": task.get("turn_id"),
            "compression_method": "latest_full_state_deterministic",
            "supersedes_prior_turns": True,
        })
        write_jsonl_atomic(compacted_path, [compacted])

