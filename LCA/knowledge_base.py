"""工作区级文档知识库。

文档解析、切片和向量检索都是确定性服务，不再由“检索智能体”二次调用
大模型决定。原文、元数据和向量均按工作区隔离保存。
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from embedding_gateway import EmbeddingGateway


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / ".lca_data" / "workspaces"
SUPPORTED_DOCUMENT_TYPES = {".pdf", ".docx", ".txt", ".md"}
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class KnowledgeBaseError(RuntimeError):
    """知识库解析、索引或检索错误。"""


@dataclass(frozen=True)
class SourceSection:
    text: str
    page: int | None = None
    heading: str | None = None
    source_anchor: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name or name in {".", ".."}:
        raise KnowledgeBaseError("文件名无效")
    return re.sub(r"[^\w.()\-\u4e00-\u9fff ]+", "_", name)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnowledgeBaseError("文本文件不是 UTF-8 或 GB18030 编码")


def _parse_pdf(data: bytes) -> list[SourceSection]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise KnowledgeBaseError("缺少 pypdf，无法读取 PDF") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        sections = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(
                    SourceSection(text=text, page=index, source_anchor=f"第 {index} 页")
                )
        return sections
    except Exception as exc:
        raise KnowledgeBaseError(f"PDF 解析失败：{exc}") from exc


def _parse_docx(data: bytes) -> list[SourceSection]:
    try:
        from docx import Document
    except ImportError as exc:
        raise KnowledgeBaseError("缺少 python-docx，无法读取 DOCX") from exc
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise KnowledgeBaseError(f"DOCX 解析失败：{exc}") from exc

    sections: list[SourceSection] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                sections.append(
                    SourceSection(text=text, heading=heading, source_anchor=heading)
                )
            buffer.clear()

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = str(getattr(paragraph.style, "name", "") or "")
        if style_name.casefold().startswith("heading"):
            flush()
            heading = text
        else:
            buffer.append(text)
    flush()
    return sections


def _parse_text(data: bytes, suffix: str) -> list[SourceSection]:
    text = _decode_text(data).replace("\r\n", "\n").strip()
    if not text:
        return []
    if suffix != ".md":
        return [SourceSection(text=text)]

    sections: list[SourceSection] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            body = "\n".join(buffer).strip()
            if body:
                sections.append(SourceSection(body, heading=heading, source_anchor=heading))
            buffer = []
            heading = match.group(1).strip()
        else:
            buffer.append(line)
    body = "\n".join(buffer).strip()
    if body:
        sections.append(SourceSection(body, heading=heading, source_anchor=heading))
    return sections or [SourceSection(text=text)]


def parse_document(filename: str, data: bytes) -> list[SourceSection]:
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_DOCUMENT_TYPES:
        raise KnowledgeBaseError(
            f"不支持 {suffix or '无扩展名'} 文件，只支持 PDF、DOCX、TXT、MD"
        )
    if not data:
        raise KnowledgeBaseError("上传文件为空")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise KnowledgeBaseError("单个文档不能超过 50 MiB")
    if suffix == ".pdf":
        sections = _parse_pdf(data)
    elif suffix == ".docx":
        sections = _parse_docx(data)
    else:
        sections = _parse_text(data, suffix)
    if not sections:
        raise KnowledgeBaseError("文档中没有可索引的文本")
    return sections


def _choose_split(text: str, target: int, minimum: int) -> int:
    """尽量在自然边界切片，同时保证单片长度可控。"""

    if len(text) <= target:
        return len(text)
    window = text[minimum:target]
    for separator in ("\n\n", "\n", "。", "！", "？", ". ", " "):
        position = window.rfind(separator)
        if position >= 0:
            return minimum + position + len(separator)
    return target


def chunk_sections(
    sections: Sequence[SourceSection],
    *,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[SourceSection]:
    if chunk_size < 200:
        raise KnowledgeBaseError("切片长度不能小于 200 字符")
    if overlap < 0 or overlap >= chunk_size:
        raise KnowledgeBaseError("切片重叠必须大于等于 0 且小于切片长度")

    chunks: list[SourceSection] = []
    for section in sections:
        text = re.sub(r"[ \t]+", " ", section.text).strip()
        start = 0
        while start < len(text):
            relative_end = _choose_split(
                text[start:], chunk_size, max(chunk_size // 2, 1)
            )
            end = min(start + relative_end, len(text))
            body = text[start:end].strip()
            if body:
                chunks.append(
                    SourceSection(
                        text=body,
                        page=section.page,
                        heading=section.heading,
                        source_anchor=section.source_anchor,
                    )
                )
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return chunks


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


class KnowledgeBaseManager:
    def __init__(
        self,
        workspace_id: str,
        *,
        data_root: Path | str = DEFAULT_DATA_ROOT,
        embedding_gateway: EmbeddingGateway | None = None,
    ) -> None:
        if not workspace_id.strip():
            raise KnowledgeBaseError("workspace_id 不能为空")
        self.workspace_id = workspace_id
        self.root = Path(data_root) / workspace_id / "knowledge"
        self.originals_dir = self.root / "originals"
        self.database_path = self.root / "knowledge.db"
        self.embedding_gateway = embedding_gateway or EmbeddingGateway()
        self.root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        """sqlite3.Connection 的原生 with 不会关闭句柄，Windows 下必须显式关闭。"""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    embedding_profile_id TEXT NOT NULL,
                    chunk_strategy TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    error TEXT,
                    UNIQUE(kb_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    page INTEGER,
                    heading TEXT,
                    source_anchor TEXT,
                    embedding_profile_id TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_kb_profile
                    ON chunks(kb_id, embedding_profile_id);
                """
            )

    def create_base(self, name: str, description: str = "") -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise KnowledgeBaseError("知识库名称不能为空")
        kb_id = uuid.uuid4().hex
        created_at = _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO knowledge_bases(id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (kb_id, clean_name, description.strip(), created_at),
            )
        return {
            "id": kb_id,
            "name": clean_name,
            "description": description.strip(),
            "created_at": created_at,
        }

    def ensure_default(self) -> dict[str, Any]:
        bases = self.list_bases()
        return bases[0] if bases else self.create_base("默认知识库", "当前工作区的通用知识")

    def list_bases(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT kb.*, COUNT(documents.id) AS document_count
                FROM knowledge_bases AS kb
                LEFT JOIN documents ON documents.kb_id = kb.id
                GROUP BY kb.id
                ORDER BY kb.created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_documents(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM documents"
        params: tuple[Any, ...] = ()
        if kb_id:
            sql += " WHERE kb_id = ?"
            params = (kb_id,)
        sql += " ORDER BY created_at DESC"
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def ingest_bytes(
        self,
        kb_id: str,
        filename: str,
        data: bytes,
        *,
        embedding_profile_id: str,
        chunk_size: int = 1000,
        overlap: int = 150,
    ) -> dict[str, Any]:
        """解析、切片并一次性入库；向量失败时不留下半成品索引。"""

        safe_name = _safe_filename(filename)
        digest = hashlib.sha256(data).hexdigest()
        with self._connection() as connection:
            if not connection.execute(
                "SELECT 1 FROM knowledge_bases WHERE id = ?", (kb_id,)
            ).fetchone():
                raise KnowledgeBaseError(f"知识库不存在：{kb_id}")
            duplicate = connection.execute(
                "SELECT id, filename FROM documents WHERE kb_id = ? AND sha256 = ?",
                (kb_id, digest),
            ).fetchone()
            if duplicate:
                raise KnowledgeBaseError(
                    f"同一文件内容已经入库：{duplicate['filename']} ({duplicate['id']})"
                )

        sections = parse_document(safe_name, data)
        chunks = chunk_sections(sections, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            raise KnowledgeBaseError("文档切片结果为空")
        vectors = self.embedding_gateway.embed_texts(
            [chunk.text for chunk in chunks],
            profile_id=embedding_profile_id,
        )
        vector_dimensions = {len(vector) for vector in vectors}
        if len(vector_dimensions) != 1 or 0 in vector_dimensions:
            raise KnowledgeBaseError("Embedding 向量维度不一致或为空")

        document_id = uuid.uuid4().hex
        created_at = _now()
        document_dir = self.originals_dir / document_id
        document_dir.mkdir(parents=True, exist_ok=False)
        original_path = document_dir / safe_name
        try:
            original_path.write_bytes(data)
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO documents(
                        id, kb_id, filename, file_type, sha256, status,
                        embedding_profile_id, chunk_strategy, chunk_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        kb_id,
                        safe_name,
                        Path(safe_name).suffix.casefold(),
                        digest,
                        embedding_profile_id,
                        f"char:{chunk_size}:overlap:{overlap}",
                        len(chunks),
                        created_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO chunks(
                        id, document_id, kb_id, chunk_index, text, page, heading,
                        source_anchor, embedding_profile_id, embedding_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            uuid.uuid4().hex,
                            document_id,
                            kb_id,
                            index,
                            chunk.text,
                            chunk.page,
                            chunk.heading,
                            chunk.source_anchor,
                            embedding_profile_id,
                            json.dumps(vector),
                        )
                        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
                    ],
                )
        except Exception:
            shutil.rmtree(document_dir, ignore_errors=True)
            raise
        return {
            "document_id": document_id,
            "filename": safe_name,
            "kb_id": kb_id,
            "chunk_count": len(chunks),
            "embedding_profile_id": embedding_profile_id,
            "vector_dimensions": vector_dimensions.pop(),
            "original_path": str(original_path),
        }

    def search(
        self,
        query: str,
        *,
        kb_ids: Sequence[str] | None = None,
        embedding_profile_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            raise KnowledgeBaseError("检索问题不能为空")
        if not 1 <= top_k <= 50:
            raise KnowledgeBaseError("top_k 必须在 1 到 50 之间")
        query_vector = self.embedding_gateway.embed_query(
            clean_query,
            profile_id=embedding_profile_id,
        )

        conditions = ["chunks.embedding_profile_id = ?"]
        params: list[Any] = [embedding_profile_id]
        if kb_ids:
            conditions.append("chunks.kb_id IN (" + ",".join("?" for _ in kb_ids) + ")")
            params.extend(kb_ids)
        where = " AND ".join(conditions)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT chunks.*, documents.filename, knowledge_bases.name AS kb_name
                FROM chunks
                JOIN documents ON documents.id = chunks.document_id
                JOIN knowledge_bases ON knowledge_bases.id = chunks.kb_id
                WHERE {where}
                """,
                params,
            ).fetchall()

        scored: list[dict[str, Any]] = []
        for row in rows:
            vector = json.loads(row["embedding_json"])
            score = _cosine_similarity(query_vector, vector)
            if score < -0.5:  # 维度不一致时跳过旧索引，而不是返回伪结果。
                continue
            anchor = row["source_anchor"] or (
                f"第 {row['page']} 页" if row["page"] is not None else None
            )
            citation = row["filename"] + (f"，{anchor}" if anchor else "")
            scored.append(
                {
                    "chunk_id": row["id"],
                    "document_id": row["document_id"],
                    "kb_id": row["kb_id"],
                    "kb_name": row["kb_name"],
                    "filename": row["filename"],
                    "chunk_index": row["chunk_index"],
                    "text": row["text"],
                    "page": row["page"],
                    "heading": row["heading"],
                    "citation": citation,
                    "score": round(score, 6),
                }
            )
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
