"""Build a deterministic UE asset catalog without starting Unreal Engine."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import write_json_atomic, write_jsonl_atomic


TYPE_HINTS = (
    (("matins", "materialinstance", "materials/instances", "mi_", "m4ue"), "material_instance_candidate"),
    (("material", "materials", "_mat", "m_"), "material_candidate"),
    (("mesh", "meshes", "sm_", "skeletal"), "mesh_candidate"),
    (("texture", "textures", "_tex", "t_"), "texture_candidate"),
    (("foliage",), "foliage_candidate"),
    (("sound", "audio"), "audio_candidate"),
)

ASSET_CLASS_MARKERS = (
    (b"BlueprintGeneratedClass", "blueprint_candidate"),
    (b"MaterialInstanceConstant", "material_instance_candidate"),
    (b"StaticMesh", "mesh_candidate"),
    (b"Texture2D", "texture_candidate"),
    (b"Material", "material_candidate"),
)


def _read_package_header(path: Path, limit: int = 2 * 1024 * 1024) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def infer_asset_type(relative_path: Path, file_path: Path | None = None) -> str:
    suffix = relative_path.suffix.lower()
    if suffix == ".umap":
        return "map"
    if suffix not in {".uasset", ".umap"}:
        return "source_file"
    if file_path is not None and suffix == ".uasset":
        # UE package name tables contain exported/imported class names. This
        # local signature is more reliable than folder naming, but still does
        # not replace Asset Registry verification.
        data = _read_package_header(file_path)
        for marker, asset_type in ASSET_CLASS_MARKERS:
            if marker in data:
                return asset_type
    lowered = relative_path.as_posix().lower()
    for hints, asset_type in TYPE_HINTS:
        if any(hint in lowered for hint in hints):
            return asset_type
    return "unclassified_uasset"


def infer_handlers(relative_path: Path, asset_type: str) -> list[str]:
    lowered = relative_path.as_posix().lower()
    handlers: list[str] = []
    if asset_type == "blueprint_candidate":
        if "road" in lowered:
            handlers.append("RoadAuto")
        if any(token in lowered for token in ("river", "water")):
            handlers.append("RiverAuto")
        if any(token in lowered for token in ("build", "airport")):
            handlers.append("BuildAuto")
        if any(token in lowered for token in ("foliage", "vegetation", "fourseason")):
            handlers.append("FoliageAuto")
    if asset_type in {"material_candidate", "material_instance_candidate"} and any(
        token in lowered for token in ("landscape", "m4")
    ):
        handlers.append("Landscaping")
    return sorted(set(handlers))


def infer_path_fields(asset_type: str, handlers: list[str]) -> list[str]:
    if not handlers:
        return []
    if asset_type == "blueprint_candidate":
        return ["actor_path"]
    if asset_type in {"material_candidate", "material_instance_candidate"} and "Landscaping" in handlers:
        return ["material_path"]
    return []


def build_catalog(
    source_root: str | Path,
    output_path: str | Path,
    manifest_path: str | Path,
    hash_files: bool = False,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Asset root does not exist: {root}")

    rows: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    top_folder_counts: Counter[str] = Counter()
    total_bytes = 0

    for file_path in sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.as_posix().lower()):
        relative = file_path.relative_to(root)
        asset_type = infer_asset_type(relative, file_path)
        relative_no_suffix = relative.with_suffix("").as_posix()
        ue_package_path = f"/Game/EnvModels/{relative_no_suffix}"
        ue_object_path = f"{ue_package_path}.{file_path.stem}"
        handlers = infer_handlers(relative, asset_type)
        compatible_path_fields = infer_path_fields(asset_type, handlers)
        generated_class_path = f"{ue_package_path}.{file_path.stem}_C" if asset_type == "blueprint_candidate" else None
        stat = file_path.stat()
        record: dict[str, Any] = {
            "resource_id": "envmodels_" + hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:20],
            "knowledge_type": "resource",
            "name": file_path.stem,
            "aliases": [file_path.stem.replace("_", " ")],
            "source_root": str(root),
            "relative_path": relative.as_posix(),
            "filesystem_path": str(file_path),
            "ue_package_path": ue_package_path,
            "ue_object_path_candidate": ue_object_path,
            "ue_generated_class_path_candidate": generated_class_path,
            "preferred_plugin_path_candidate": generated_class_path or ue_object_path,
            "extension": file_path.suffix.lower(),
            "inferred_asset_type": asset_type,
            "top_folder": relative.parts[0] if relative.parts else "",
            "compatible_handlers": handlers,
            "compatible_path_fields": compatible_path_fields,
            "candidate_for_plugin_path": bool(compatible_path_fields),
            "index_admission": True,
            "size_bytes": stat.st_size,
            "last_write_time_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "verification_status": "filesystem_verified_ue_unverified",
            "verification_notes": "文件存在已核实；资产类、对象类路径和插件可实例化性仅由命名推断，需在UE Asset Registry中核实。",
        }
        if hash_files:
            record["sha256"] = _sha256(file_path)
        rows.append(record)
        type_counts[asset_type] += 1
        extension_counts[file_path.suffix.lower()] += 1
        top_folder_counts[record["top_folder"]] += 1
        total_bytes += stat.st_size

    write_jsonl_atomic(output_path, rows)
    manifest = {
        "catalog_version": "envmodels-filesystem-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root),
        "mount_mapping": {str(root): "/Game/EnvModels"},
        "file_count": len(rows),
        "total_bytes": total_bytes,
        "extension_counts": dict(sorted(extension_counts.items())),
        "inferred_asset_type_counts": dict(sorted(type_counts.items())),
        "top_folder_counts": dict(sorted(top_folder_counts.items())),
        "file_hashes_computed": hash_files,
        "limitations": [
            "未启动Unreal Engine，因此无法从Asset Registry获得真实AssetClass、GeneratedClass或依赖关系。",
            "ue_object_path_candidate由Content挂载规则推导，不等于已验证的Actor/Blueprint类路径。",
            "Blueprint的ue_generated_class_path_candidate按_Name_C规则推导，仍需Asset Registry或运行时核实。",
            "全部文件均登记，但只有candidate_for_plugin_path=true的记录可进入插件路径候选。",
            "index_admission=true表示记录可供资源检索；不表示其候选对象路径可直接作为actor_path。",
        ],
    }
    write_json_atomic(manifest_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--hash-files", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_catalog(args.source_root, args.output, args.manifest, args.hash_files), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
