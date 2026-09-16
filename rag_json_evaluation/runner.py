"""Offline experiment runner: decision -> retrieval evidence -> coding -> JSON."""

from __future__ import annotations

import traceback
from pathlib import Path
from time import perf_counter
from typing import Any

from .agents import CodingAgent, DecisionAgent
from .context_memory import ContextMemoryStore
from .embedding import EmbeddingClient
from .io_utils import content_hash, load_jsonl, utc_now, write_json_atomic
from .model_client import ModelClient
from .retrieval import RetrievalAgent
from .validation import validate_scene_plan


def _code_snapshot_hash() -> str:
    package_root = Path(__file__).resolve().parent
    included: dict[str, str] = {}
    for pattern in ("*.py", "configs/*.yaml", "prompts/*.txt", "schemas/*.json"):
        for path in sorted(package_root.glob(pattern)):
            if path.is_file():
                included[str(path.relative_to(package_root))] = path.read_text(encoding="utf-8")
    return content_hash(included)


def _safe_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_experiment(
    config: dict[str, Any],
    tasks_path: str | Path,
    run_id: str,
    repeats: int = 1,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """Run one group. This function deliberately contains no Unreal/socket code."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    tasks = load_jsonl(tasks_path)
    if not tasks:
        raise ValueError(f"No tasks found in {tasks_path}")

    seen_task_ids: set[str] = set()
    last_turn_by_conversation: dict[str, int] = {}
    for task in tasks:
        required = ("task_id", "category", "conversation_id", "turn_id", "user_prompt")
        missing = [field for field in required if field not in task]
        if missing:
            raise ValueError(f"Task record is missing required fields {missing}: {task}")
        task_id = str(task["task_id"])
        if task_id in seen_task_ids:
            raise ValueError(f"Duplicate task_id: {task_id}")
        seen_task_ids.add(task_id)
        conversation_id = task.get("conversation_id")
        if conversation_id:
            turn_id = int(task["turn_id"])
            previous = last_turn_by_conversation.get(str(conversation_id), 0)
            if turn_id != previous + 1:
                raise ValueError(
                    f"Conversation {conversation_id!r} must be contiguous from turn 1; got {turn_id} after {previous}"
                )
            last_turn_by_conversation[str(conversation_id)] = turn_id

    output_root = Path(config["paths"]["outputs_root"]) / run_id / config["group"]
    if output_root.exists():
        raise FileExistsError(f"Refusing to overwrite existing run directory: {output_root}")
    output_root.mkdir(parents=True)

    model = ModelClient(config)
    embedder = EmbeddingClient(config) if config["rag_enabled"] else None
    retrieval_agent = RetrievalAgent(config, embedder)
    decision_agent = DecisionAgent(config, model)
    coding_agent = CodingAgent(config, model)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "group": config["group"],
        "started_at": utc_now(),
        "task_count": len(tasks),
        "repeats": repeats,
        "completed": 0,
        "failed": 0,
        "valid_scene_plans": 0,
    }
    write_json_atomic(output_root / "run_manifest.json", {
        **summary,
        "config": config,
        "tasks_path": str(Path(tasks_path).resolve()),
        "tasks_hash": content_hash(tasks),
        "code_snapshot_hash": _code_snapshot_hash(),
        "llm_model": model.model,
        "embedding_model": embedder.model if embedder else None,
        "unreal_execution": False,
        "agent_chain": ["decision_agent", "retrieval_agent", "coding_agent", "static_validator"],
    })

    for repeat in range(1, repeats + 1):
        context_store = ContextMemoryStore(output_root / f"repeat-{repeat:02d}" / "context_memory")
        for task in tasks:
            task_dir = output_root / f"repeat-{repeat:02d}" / str(task["task_id"])
            task_dir.mkdir(parents=True, exist_ok=False)
            write_json_atomic(task_dir / "input.json", task)
            stage = "decision"
            try:
                conversation_id = task.get("conversation_id")
                runtime_context = context_store.get_compacted(str(conversation_id) if conversation_id else None)
                decision_started = perf_counter()
                decision = decision_agent.run(task)
                decision_ms = round((perf_counter() - decision_started) * 1000, 3)
                write_json_atomic(task_dir / "decision.json", decision.value)
                _safe_text(task_dir / "decision_raw.txt", decision.raw_text)

                stage = "retrieval"
                retrieval_started = perf_counter()
                retrieval = retrieval_agent.retrieve(
                    task,
                    runtime_context,
                    query=decision.value.get("retrieval_query") or task["user_prompt"],
                )
                retrieval_ms = round((perf_counter() - retrieval_started) * 1000, 3)
                write_json_atomic(task_dir / "retrieval.json", retrieval)

                stage = "coding"
                coding_started = perf_counter()
                coding = coding_agent.run(task, decision.value, retrieval)
                coding_ms = round((perf_counter() - coding_started) * 1000, 3)
                write_json_atomic(task_dir / "scene_plan.json", coding.value)
                _safe_text(task_dir / "coding_raw.txt", coding.raw_text)

                stage = "validation"
                validation_started = perf_counter()
                validation = validate_scene_plan(
                    coding.value,
                    config["paths"]["schema_path"],
                    config["paths"]["plugin_contract_path"],
                    task,
                    retrieval,
                )
                validation_ms = round((perf_counter() - validation_started) * 1000, 3)
                write_json_atomic(task_dir / "validation.json", validation)
                write_json_atomic(task_dir / "trace.json", {
                    "status": "completed",
                    "group": config["group"],
                    "repeat": repeat,
                    "task_id": task["task_id"],
                    "retrieval_routes": retrieval["routes"],
                    "retrieved_chunk_ids": [item["chunk_id"] for item in retrieval["results"]],
                    "decision_usage": decision.usage,
                    "coding_usage": coding.usage,
                    "latency_ms": {
                        "decision": decision_ms,
                        "retrieval": retrieval_ms,
                        "coding": coding_ms,
                        "validation": validation_ms,
                        "total": round(decision_ms + retrieval_ms + coding_ms + validation_ms, 3),
                    },
                    "scene_plan_valid": validation["valid"],
                    "unreal_execution": False,
                    "completed_at": utc_now(),
                })
                summary["completed"] += 1
                summary["valid_scene_plans"] += int(validation["valid"])
                if conversation_id:
                    context_store.record_turn(task, decision.value, retrieval, coding.value, validation)
            except Exception as error:
                summary["failed"] += 1
                write_json_atomic(task_dir / "error.json", {
                    "status": "failed",
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                    "unreal_execution": False,
                    "failed_at": utc_now(),
                })
                if fail_fast:
                    summary["finished_at"] = utc_now()
                    write_json_atomic(output_root / "summary.json", summary)
                    raise

    summary["finished_at"] = utc_now()
    write_json_atomic(output_root / "summary.json", summary)
    return summary
