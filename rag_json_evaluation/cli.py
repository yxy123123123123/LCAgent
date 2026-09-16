"""Command line interface for setup, indexing, offline generation, and validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, load_config
from .embedding import EmbeddingClient
from .index_store import build_indexes
from .inspection import inspect_setup
from .io_utils import load_json
from .runner import run_experiment
from .validation import validate_scene_plan


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="LCAgent RAG-to-ScenePlan offline evaluation")
    commands = root.add_subparsers(dest="command", required=True)

    inspect_cmd = commands.add_parser("inspect", help="Read-only setup and missing-material audit")
    inspect_cmd.add_argument("--group", choices=("G0", "G1", "G2", "G3"), default="G3")
    inspect_cmd.add_argument("--tasks", default=str(PACKAGE_ROOT / "data" / "tasks.jsonl"))

    build_cmd = commands.add_parser("build-indexes", help="Call embedding API and build one group's indexes")
    build_cmd.add_argument("--group", choices=("G1", "G2", "G3"), required=True)
    build_cmd.add_argument("--batch-size", type=int, default=32)

    run_cmd = commands.add_parser("run", help="Run agents offline and stop after ScenePlan validation")
    run_cmd.add_argument("--group", choices=("G0", "G1", "G2", "G3"), required=True)
    run_cmd.add_argument("--tasks", default=str(PACKAGE_ROOT / "data" / "tasks.jsonl"))
    run_cmd.add_argument("--run-id", required=True)
    run_cmd.add_argument("--repeats", type=int, default=1)
    run_cmd.add_argument("--fail-fast", action="store_true")

    validate_cmd = commands.add_parser("validate", help="Validate one existing ScenePlan without API/UE")
    validate_cmd.add_argument("--scene-plan", required=True)
    validate_cmd.add_argument("--task")
    validate_cmd.add_argument("--retrieval")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "inspect":
        report = inspect_setup(load_config(args.group), args.tasks)
        _print(report)
        return 0 if report["ready"] else 2
    if args.command == "build-indexes":
        config = load_config(args.group)
        _print(build_indexes(config, EmbeddingClient(config), args.batch_size))
        return 0
    if args.command == "run":
        _print(run_experiment(
            load_config(args.group), args.tasks, args.run_id, args.repeats, args.fail_fast
        ))
        return 0
    if args.command == "validate":
        config = load_config("G0")
        result = validate_scene_plan(
            load_json(args.scene_plan),
            config["paths"]["schema_path"],
            config["paths"]["plugin_contract_path"],
            load_json(args.task) if args.task else None,
            load_json(args.retrieval) if args.retrieval else None,
        )
        _print(result)
        return 0 if result["valid"] else 3
    return 1


if __name__ == "__main__":
    sys.exit(main())

