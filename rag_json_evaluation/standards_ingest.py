"""Extract source-grounded section records from downloaded public standards."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .io_utils import write_json_atomic, write_jsonl_atomic


SOURCE_METADATA: dict[str, dict[str, Any]] = {
    "OGC_CDB_2.0_Core_23-034.pdf": {
        "standard_id": "OGC-23-034",
        "title": "OGC CDB Version 2 - Part 1: Core Standard",
        "publisher": "Open Geospatial Consortium",
        "version": "2.0",
        "publication_date": "2026-01-06",
        "source_url": "https://docs.ogc.org/is/23-034/23-034.pdf",
        "scope_tags": ["synthetic_environment", "resource_path", "metadata", "crs", "geometry", "tiling", "versioning"],
    },
    "OGC_CDB_1.2_Terms_15-112r4.pdf": {
        "standard_id": "OGC-15-112r4",
        "title": "OGC CDB Terms and Definitions (Normative)",
        "publisher": "Open Geospatial Consortium",
        "version": "1.2",
        "publication_date": "2021-02-26",
        "source_url": "https://docs.ogc.org/is/15-112r4/15-112r4.pdf",
        "scope_tags": ["synthetic_environment", "terminology", "data_store"],
    },
    "OGC_CityGML_3.0_Conceptual_Model_20-010.pdf": {
        "standard_id": "OGC-20-010",
        "title": "OGC CityGML 3.0 Part 1: Conceptual Model Standard",
        "publisher": "Open Geospatial Consortium",
        "version": "3.0.0",
        "publication_date": "2021-09-13",
        "source_url": "https://docs.ogc.org/is/20-010/20-010.pdf",
        "scope_tags": ["building", "road", "railway", "bridge", "waterbody", "vegetation", "terrain", "lod", "3d_city"],
    },
    "OGC_GeoTIFF_1.1_19-008r4.pdf": {
        "standard_id": "OGC-19-008r4",
        "title": "OGC GeoTIFF Standard",
        "publisher": "Open Geospatial Consortium",
        "version": "1.1",
        "publication_date": "2019-09-14",
        "source_url": "https://docs.ogc.org/is/19-008r4/19-008r4.pdf",
        "scope_tags": ["geotiff", "dem", "raster", "crs", "georeferencing"],
    },
}

HEADING_RE = re.compile(r"^(?P<number>(?:\d+\.)*\d+)\.\s+(?P<title>[^\n]{2,160})$", re.MULTILINE)


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_toc(title: str, body: str) -> bool:
    return (
        title.startswith("[[PAGE")
        or ". . ." in title
        or title.count(".") > 12
        or bool(re.search(r"\.{4,}\s*\d*\s*$", title))
        or bool(re.search(r"\.{4,}\s*\d*\s*$", body[:240]))
    )


def _split_body(text: str, target_chars: int = 2400, overlap_chars: int = 400) -> list[str]:
    if len(text) <= target_chars:
        return [text]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        step = target_chars - overlap_chars
        return [text[start:start + target_chars] for start in range(0, len(text), step)]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= target_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _section_level(number: str) -> int:
    return number.count(".") + 1


def _parent_section(number: str) -> str | None:
    parts = number.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def extract_pdf(path: Path, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = path.read_bytes()
    reader = PdfReader(path)
    pages = [_clean_text(page.extract_text() or "") for page in reader.pages]
    page_offsets: list[tuple[int, int, int]] = []
    assembled: list[str] = []
    cursor = 0
    for page_number, page_text in enumerate(pages, start=1):
        if assembled:
            assembled.append("\n\n")
            cursor += 2
        start = cursor
        assembled.append(page_text)
        cursor += len(page_text)
        page_offsets.append((start, cursor, page_number))
    full_text = "".join(assembled)
    matches = [match for match in HEADING_RE.finditer(full_text) if not _looks_like_toc(match.group("title"), full_text[match.end():match.end()+240])]
    # CDB Terms is an alphabetic glossary rather than numbered clauses. Keep
    # one source-grounded page record per glossary page.
    if metadata["standard_id"] == "OGC-15-112r4":
        records = []
        for page_number, page_text in enumerate(pages[6:24], start=7):
            if len(page_text) < 80:
                continue
            records.append({
                "clause_id": f"{metadata['standard_id']}::glossary-page-{page_number:03d}",
                "knowledge_type": "standard",
                "standard_id": metadata["standard_id"],
                "standard_title": metadata["title"],
                "publisher": metadata["publisher"],
                "version": metadata["version"],
                "publication_date": metadata["publication_date"],
                "source_url": metadata["source_url"],
                "source_file": path.name,
                "source_sha256": hashlib.sha256(data).hexdigest(),
                "section_number": f"glossary-page-{page_number}",
                "section_title": "Terms and Definitions",
                "section_level": 2,
                "parent_section": "1",
                "part_index": 1,
                "part_count": 1,
                "page_start": page_number,
                "page_end": page_number,
                "scope_tags": metadata["scope_tags"],
                "text": page_text,
                "verification_status": "downloaded_official_pdf_text_extracted",
                "applicability": "公开技术依据；不得表述为中国军用标准或直接战术规则。",
            })
        manifest = {
            **metadata,
            "source_file": path.name,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "pdf_signature_valid": data[:4] == b"%PDF",
            "page_count": len(reader.pages),
            "extracted_section_count": len(records),
        }
        return records, manifest
    records: list[dict[str, Any]] = []
    seen_sections: set[str] = set()

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(full_text)
        body = _clean_text(full_text[start:end])
        # Avoid indexing a heading detection that contains only a page header or TOC fragment.
        if len(body) < 80:
            continue
        section_number = match.group("number")
        title = match.group("title").strip()
        text = body.strip()
        if match.start() < page_offsets[min(9, len(page_offsets) - 1)][1] and (
            ". . ." in text[:500] or re.search(r"\.{4,}\s*\d", text[:500])
        ):
            continue
        if section_number in seen_sections:
            continue
        seen_sections.add(section_number)
        section_pages = [page_number for page_start, page_end, page_number in page_offsets if page_start < end and page_end > match.start()]
        if not text:
            continue
        parts = _split_body(text)
        for part_index, part in enumerate(parts, start=1):
            part_suffix = f"::part-{part_index:02d}" if len(parts) > 1 else ""
            records.append({
                "clause_id": f"{metadata['standard_id']}::{section_number}{part_suffix}",
                "knowledge_type": "standard",
                "standard_id": metadata["standard_id"],
                "standard_title": metadata["title"],
                "publisher": metadata["publisher"],
                "version": metadata["version"],
                "publication_date": metadata["publication_date"],
                "source_url": metadata["source_url"],
                "source_file": path.name,
                "source_sha256": hashlib.sha256(data).hexdigest(),
                "section_number": section_number,
                "section_title": title,
                "section_level": _section_level(section_number),
                "parent_section": _parent_section(section_number),
                "part_index": part_index,
                "part_count": len(parts),
                "page_start": min(section_pages) if section_pages else None,
                "page_end": max(section_pages) if section_pages else None,
                "scope_tags": metadata["scope_tags"],
                "text": part,
                "verification_status": "downloaded_official_pdf_text_extracted",
                "applicability": "公开技术依据；不得表述为中国军用标准或直接战术规则。",
            })

    manifest = {
        **metadata,
        "source_file": path.name,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "pdf_signature_valid": data[:4] == b"%PDF",
        "page_count": len(reader.pages),
        "extracted_section_count": len(records),
    }
    return records, manifest


def ingest(source_dir: str | Path, output: str | Path, manifest_output: str | Path) -> dict[str, Any]:
    root = Path(source_dir)
    records: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    for filename, metadata in SOURCE_METADATA.items():
        path = root / filename
        if not path.exists():
            manifests.append({**metadata, "source_file": filename, "status": "not_downloaded"})
            continue
        source_records, source_manifest = extract_pdf(path, metadata)
        records.extend(source_records)
        manifests.append({**source_manifest, "status": "ingested"})
    write_jsonl_atomic(output, records)
    overall = {
        "ingest_version": "official-pdf-sections-v1",
        "record_count": len(records),
        "sources": manifests,
        "limitations": [
            "章节识别来自PDF文本层的编号标题规则，正式冻结前需抽样人工复核。",
            "公开OGC资料只作为合成环境与地理空间技术依据，不替代论文列出的GJB和内部规范。",
        ],
    }
    write_json_atomic(manifest_output, overall)
    return overall


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(ingest(args.source_dir, args.output, args.manifest), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
