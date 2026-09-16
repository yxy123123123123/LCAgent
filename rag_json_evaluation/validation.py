"""Schema, UE contract, ordering, identity, and provenance validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .io_utils import load_json


OBJECT_HANDLER = {
    "terrain": "Landscaping",
    "road": "RoadAuto",
    "river": "RiverAuto",
    "building_area": "BuildAuto",
}

REQUIRED_CREATE_COMMANDS = {
    "terrain": {"DEM", "Execute"},
    "road": {"Actor", "Points", "Execute"},
    "river": {"Actor", "Points", "Execute"},
    "building_area": {"Actor", "Points", "Execute"},
}

PATH_FIELDS = ("file_paths", "material_path", "actor_path", "layer_path")


def _issue(code: str, message: str, path: str = "", severity: str = "error") -> dict[str, str]:
    return {"severity": severity, "code": code, "path": path, "message": message}


def validate_scene_plan(
    scene_plan: dict[str, Any],
    schema_path: str | Path,
    plugin_contract_path: str | Path,
    task: dict[str, Any] | None = None,
    retrieval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = load_json(schema_path)
    contract = load_json(plugin_contract_path)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    validator = Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(scene_plan), key=lambda item: list(item.absolute_path))
    for error in schema_errors:
        json_path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
        errors.append(_issue("JSON_SCHEMA", error.message, json_path))

    if task:
        for field in ("task_id", "conversation_id", "turn_id"):
            if scene_plan.get(field) != task.get(field):
                errors.append(_issue(
                    "TASK_IDENTITY_MISMATCH",
                    f"ScenePlan {field}={scene_plan.get(field)!r} does not match task value {task.get(field)!r}",
                    f"$.{field}",
                ))

    object_ids: set[str] = set()
    evidence_text = str(task.get("user_prompt", "")) if task else ""
    retrieved_path_policies: dict[str, dict[str, Any]] = {}
    if retrieval:
        result_texts = [str(item.get("text", "")) for item in retrieval.get("results", [])]
        evidence_text += "\n" + "\n".join(result_texts)
        for text in result_texts:
            try:
                payload = text.split("\n", 1)[1] if text.startswith("[knowledge_type=") else text
                record = json.loads(payload)
            except (IndexError, ValueError, TypeError):
                continue
            for key in (
                "ue_path", "ue_package_path", "ue_object_path_candidate",
                "ue_generated_class_path_candidate", "preferred_plugin_path_candidate", "filesystem_path",
            ):
                if record.get(key):
                    retrieved_path_policies[str(record[key])] = record
    grounding_evidence_available = bool(task or retrieval)
    if not grounding_evidence_available:
        warnings.append(_issue(
            "PATH_GROUNDING_SKIPPED",
            "No task/retrieval evidence was supplied; resource path provenance was not checked",
            "$",
            "warning",
        ))

    for object_index, scene_object in enumerate(scene_plan.get("objects", [])):
        base_path = f"$.objects[{object_index}]"
        object_id = scene_object.get("object_id")
        if object_id in object_ids:
            errors.append(_issue("DUPLICATE_OBJECT_ID", f"Duplicate object_id: {object_id}", f"{base_path}.object_id"))
        object_ids.add(object_id)

        object_type = scene_object.get("object_type")
        operation = scene_object.get("operation")
        commands = scene_object.get("commands", [])
        expected_handler = OBJECT_HANDLER.get(object_type)
        command_types: list[str] = []
        for command_index, command in enumerate(commands):
            command_path = f"{base_path}.commands[{command_index}]"
            if expected_handler and command.get("type") != expected_handler:
                errors.append(_issue(
                    "HANDLER_OBJECT_MISMATCH",
                    f"{object_type} requires handler {expected_handler}, got {command.get('type')}",
                    f"{command_path}.type",
                ))
            params = command.get("params", {})
            command_type = params.get("command_type")
            if command_type:
                command_types.append(command_type)
            for field in PATH_FIELDS:
                path_value = params.get(field)
                if grounding_evidence_available and path_value and path_value not in evidence_text:
                    errors.append(_issue(
                        "UNSUPPORTED_RESOURCE_PATH",
                        f"Path {path_value!r} is absent from the user input and retrieved evidence",
                        f"{command_path}.params.{field}",
                    ))
                policy = retrieved_path_policies.get(str(path_value)) if path_value else None
                if policy and policy.get("index_admission") is False:
                    errors.append(_issue(
                        "RESOURCE_NOT_ADMITTED",
                        f"Path {path_value!r} belongs to a resource record excluded from the experiment index",
                        f"{command_path}.params.{field}",
                    ))
                allowed_fields = policy.get("compatible_path_fields") if policy else None
                wrong_field = (
                    isinstance(allowed_fields, list) and field not in allowed_fields
                ) or (
                    allowed_fields is None
                    and field in {"actor_path", "material_path", "layer_path"}
                    and not policy.get("candidate_for_plugin_path", True)
                    if policy else False
                )
                if policy and wrong_field:
                    errors.append(_issue(
                        "RESOURCE_NOT_PLUGIN_PATH_CANDIDATE",
                        f"Path {path_value!r} is searchable metadata but is not admitted for field {field}",
                        f"{command_path}.params.{field}",
                    ))
                if policy and "ue_unverified" in str(policy.get("verification_status", "")):
                    warnings.append(_issue(
                        "UE_RESOURCE_PATH_UNVERIFIED",
                        f"Path {path_value!r} was inferred from disk layout and has not been verified by UE Asset Registry",
                        f"{command_path}.params.{field}",
                        "warning",
                    ))

        if commands:
            execute_positions = [index for index, value in enumerate(command_types) if value == "Execute"]
            if len(execute_positions) != 1 or execute_positions[0] != len(command_types) - 1:
                errors.append(_issue(
                    "EXECUTE_ORDER",
                    "Exactly one Execute command is required and it must be last",
                    f"{base_path}.commands",
                ))

            handler_spec = contract.get("handlers", {}).get(expected_handler, {})
            recommended = handler_spec.get("recommended_order", [])
            positions = [recommended.index(value) for value in command_types if value in recommended]
            if positions != sorted(positions):
                errors.append(_issue("COMMAND_ORDER", f"Commands are not in plugin order: {recommended}", f"{base_path}.commands"))

        if operation in {"create", "modify"}:
            missing = REQUIRED_CREATE_COMMANDS.get(object_type, set()) - set(command_types)
            if missing:
                errors.append(_issue(
                    "MISSING_PLUGIN_PREREQUISITE",
                    f"Missing required commands for {object_type}: {sorted(missing)}",
                    f"{base_path}.commands",
                ))
        if operation == "delete":
            warnings.append(_issue(
                "DELETE_NOT_IMPLEMENTED_BY_PLUGIN",
                "The inspected UE plugin has no delete command; this object is an offline tombstone only",
                base_path,
                "warning",
            ))

    schema_valid = not any(item["code"] == "JSON_SCHEMA" for item in errors)
    semantic_valid = not errors
    return {
        "valid": schema_valid and semantic_valid,
        "schema_valid": schema_valid,
        "semantic_valid": semantic_valid,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
