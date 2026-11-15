"""文献解析、分块、向量生成和原始文件归档。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import Settings
from .errors import KnowledgeError, ProviderError
from .providers import Embedder, normalize_vector
from .quality import assess_document
from .storage import Repository
from .text import normalize_text, split_text

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".csv", ".json"}


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            if sum(ord(c) < 32 and c not in "\r\n\t" for c in text) > max(1, len(text) // 100):
                raise KnowledgeError("文件包含过多二进制字符，请上传文本文件")
            return text
        except UnicodeDecodeError:
            continue
    raise KnowledgeError("文本编码无法识别，请转换为 UTF-8 后上传")


def extract_document(data: bytes, extension: str) -> tuple[str, dict]:
    metadata: dict = {}
    try:
        if extension in {".txt", ".md"}:
            return decode_text(data), metadata
        if extension == ".json":
            obj = json.loads(decode_text(data))
            if not isinstance(obj, dict) or not isinstance(
                obj.get("content", obj.get("text")), str
            ):
                raise KnowledgeError("JSON 文献必须为含 content 或 text 字符串的对象")
            metadata = {
                key: obj[key]
                for key in (
                    "title",
                    "source",
                    "source_id",
                    "url",
                    "doi",
                    "journal",
                    "year",
                    "authors",
                    "keywords",
                    "topic",
                    "id",
                    "content_type",
                    "pmid",
                    "pmcid",
                    "license",
                    "publication_types",
                    "xml_sha256",
                )
                if key in obj
            }
            acquisition = obj.get("acquisition")
            if "xml_sha256" not in metadata and isinstance(acquisition, dict):
                if isinstance(acquisition.get("xml_sha256"), str):
                    metadata["xml_sha256"] = acquisition["xml_sha256"]
            return obj.get("content", obj.get("text", "")), metadata
        if extension == ".csv":
            rows = csv.reader(io.StringIO(decode_text(data)))
            return "\n".join(" | ".join(row) for row in rows), metadata
        if extension == ".pdf":
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if len(pdf.pages) > 500:
                    raise KnowledgeError("单个 PDF 最多支持 500 页，请拆分上传")
                pages = [page.extract_text() or "" for page in pdf.pages]
                text = "\n\n".join(
                    f"[第 {index + 1} 页]\n{content}" for index, content in enumerate(pages)
                )
                if not any(content.strip() for content in pages):
                    raise KnowledgeError("PDF 未提取到文字，扫描件需要先进行 OCR")
                metadata["pages"] = len(pdf.pages)
                return text, metadata
        if extension == ".docx":
            from docx import Document

            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                if sum(item.file_size for item in bundle.infolist()) > 100 * 1024 * 1024:
                    raise KnowledgeError("Word 解压后体积过大，请拆分上传")
            document = Document(io.BytesIO(data))
            from docx.table import Table

            paragraphs = []
            for block in document.iter_inner_content():
                if isinstance(block, Table):
                    paragraphs.extend(
                        " | ".join(cell.text for cell in row.cells) for row in block.rows
                    )
                else:
                    paragraphs.append(block.text)
            return "\n\n".join(paragraphs), metadata
    except KnowledgeError:
        raise
    except ImportError as exc:
        raise KnowledgeError("文档解析依赖未安装，请安装 requirements.txt") from exc
    except Exception as exc:
        raise KnowledgeError("文件格式损坏或内容无法解析，请检查文件") from exc
    raise KnowledgeError("不支持的文档格式")


def clean_metadata(metadata: dict) -> dict:
    if metadata.get("content_type") is not None and not isinstance(metadata["content_type"], str):
        raise KnowledgeError("资料类型必须为字符串")
    result = {}
    for key in (
        "title",
        "source",
        "source_id",
        "url",
        "doi",
        "journal",
        "year",
        "authors",
        "keywords",
        "topic",
        "pages",
        "id",
        "content_type",
        "pmid",
        "pmcid",
        "license",
        "publication_types",
        "xml_sha256",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, int, float, list)):
            if isinstance(value, list):
                value = [str(item)[:200] for item in value[:50]]
            if isinstance(value, str):
                value = value[:2000]
            result[key] = value
    if result.get("url"):
        url = urlsplit(str(result["url"]))
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            raise KnowledgeError("来源 URL 必须为不含凭据的 HTTP(S) 地址")
    return result


class IngestionService:
    def __init__(self, repository: Repository, embedder: Embedder, settings: Settings):
        self.repository, self.embedder, self.settings = repository, embedder, settings

    def ingest(
        self,
        filename: str,
        data: bytes,
        metadata: dict | None = None,
        progress: Callable | None = None,
    ) -> dict:
        notify = progress or (lambda stage, percent: None)
        filename = Path(str(filename).replace("\\", "/")).name
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise KnowledgeError("支持 TXT、Markdown、PDF、DOCX、CSV、JSON 文献")
        if not data or len(data) > self.settings.max_upload_bytes:
            raise KnowledgeError("文件不能为空，且不能超过 25 MB")
        digest = hashlib.sha256(data).hexdigest()
        existing = self.repository.find_by_hash(digest)
        if existing:
            return {
                "document": self.repository.document(existing["id"], False, False),
                "duplicate": True,
            }
        notify("解析文献", 15)
        raw, extracted = extract_document(data, extension)
        text = normalize_text(raw)
        if len(text) < 10:
            raise KnowledgeError("文献可提取文本过少，至少需要 10 个字符")
        if len(text) > self.settings.max_text_chars:
            raise KnowledgeError("提取文本超过单篇上限，请拆分文献")
        meta = clean_metadata({**extracted, **(metadata or {})})
        meta.setdefault("source_id", Path(filename).stem)
        meta["quality"] = assess_document(text, meta)
        meta["content_type"] = meta["quality"]["content_type"]
        meta["processing"] = {
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "version": "2.1.0",
        }
        title = str(meta.get("title") or Path(filename).stem)[:400]
        document_id = "doc_" + digest[:24]
        notify("切分文本", 30)
        chunks = [
            {**asdict(chunk), "id": f"{document_id}_{chunk.ordinal:05d}"}
            for chunk in split_text(text, self.settings.chunk_size, self.settings.chunk_overlap)
        ]
        for chunk in chunks:
            if extension == ".pdf":
                import re

                pages = list(re.finditer(r"\[第 (\d+) 页\]", text[: chunk["end"]]))
                first = [p for p in pages if p.start() <= chunk["start"]]
                start_page = int(first[-1].group(1)) if first else 1
                end_page = int(pages[-1].group(1)) if pages else start_page
                chunk["locator"] = f"第 {start_page}–{end_page} 页 · " + chunk["locator"]
            elif extension == ".docx":
                chunk["locator"] = (
                    f"正文块 {text[: chunk['start']].count(chr(10) * 2) + 1} · " + chunk["locator"]
                )
        notify("生成向量", 45)
        vectors = self.embedder.embed_batch([chunk["text"] for chunk in chunks])
        document = {
            "id": document_id,
            "content_hash": digest,
            "filename": filename[:240],
            "title": title,
            "text": text,
            "metadata": meta,
            "file_type": extension,
            "byte_size": len(data),
        }
        uploads = self.settings.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / (digest + extension)
        with tempfile.NamedTemporaryFile(dir=uploads, delete=False) as handle:
            handle.write(data)
            temp = Path(handle.name)
        try:
            os.replace(temp, target)
            notify("保存文献与索引", 90)
            self.repository.insert_document(document, chunks, vectors, self.embedder.fingerprint)
        finally:
            temp.unlink(missing_ok=True)
        notify("入库完成", 100)
        return {
            "document": self.repository.document(document_id, False, False),
            "chunk_count": len(chunks),
            "duplicate": False,
        }

    def reindex(self, progress: Callable | None = None) -> dict:
        notify = progress or (lambda stage, percent: None)
        revision = self.repository.revision
        rows = self.repository.index_rows()
        vectors = []
        for start in range(0, len(rows), 16):
            notify("重建向量索引", int(start / max(len(rows), 1) * 90))
            batch = rows[start : start + 16]
            received = self.embedder.embed_batch([row["text"] for row in batch])
            if len(received) != len(batch):
                raise ProviderError("重建向量数量不一致，原索引已保留")
            for vector in received:
                vectors.append(normalize_vector(vector, len(vectors[0]) if vectors else None))
        self.repository.replace_vectors(
            [(row["id"], vector) for row, vector in zip(rows, vectors)],
            self.embedder.fingerprint,
            revision,
        )
        notify("索引完成", 100)
        return {"chunks": len(rows), "fingerprint": self.embedder.fingerprint}

    def import_directory(self, directory: Path, progress: Callable | None = None) -> dict:
        """显式迁移已有语料；JSON 元数据和同名文本优先合并为一份文献。"""
        directory = Path(directory).resolve()
        if not directory.is_dir():
            raise KnowledgeError("语料目录不存在")
        paths = sorted(
            p
            for p in directory.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        json_stems = {p.with_suffix("") for p in paths if p.suffix.lower() == ".json"}
        paths = [
            p for p in paths if p.suffix.lower() != ".txt" or p.with_suffix("") not in json_stems
        ]
        results = {"imported": 0, "duplicates": 0, "failed": [], "total": len(paths)}
        for index, path in enumerate(paths):
            if progress:
                progress(f"导入 {index + 1}/{len(paths)}", int(index / max(len(paths), 1) * 100))
            try:
                if path.stat().st_size > self.settings.max_upload_bytes:
                    raise KnowledgeError("文件超过上传体积上限")
                result = self.ingest(path.name, path.read_bytes())
                results["duplicates" if result["duplicate"] else "imported"] += 1
            except KnowledgeError as exc:
                results["failed"].append({"filename": path.name, "error": str(exc)})
        return results
