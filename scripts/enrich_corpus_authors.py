"""从已登记的原始 JATS XML 为独立子版本补齐作者元数据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from scripts.expand_open_corpus import (
    article_from_fulltext,
    file_sha256,
    save_json_atomic,
    utc_now,
)


def read_documents(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return {
        row["id"]: row
        for row in connection.execute(
            "SELECT id,content_hash,filename,title,text,metadata,file_type,byte_size "
            "FROM documents"
        )
    }


def open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def table_digest(connection: sqlite3.Connection, table: str) -> str:
    if table not in {"chunks", "embeddings"}:
        raise ValueError("只允许核对分块与向量表")
    digest = hashlib.sha256()
    key = "id" if table == "chunks" else "chunk_id"
    for row in connection.execute(f"SELECT * FROM {table} ORDER BY {key}"):
        for value in row:
            if value is None:
                kind, payload = b"0", b""
            elif isinstance(value, bytes):
                kind, payload = b"b", value
            else:
                kind, payload = b"t", str(value).encode("utf-8")
            digest.update(kind)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def expected_authors(batches: list[Path]) -> tuple[dict[str, dict], list[dict]]:
    expected = {}
    sources = []
    for batch in batches:
        report_path = batch / "acquisition_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        sources.append({"batch": str(batch), "acquisition_sha256": file_sha256(report_path)})
        for item in report["items"]:
            pmcid = item["pmcid"]
            xml_path = batch / "raw_xml" / f"{pmcid}.xml"
            if pmcid in expected or file_sha256(xml_path) != item["xml_sha256"]:
                raise ValueError(f"重复 PMCID 或原始 XML 摘要不一致：{pmcid}")
            article = article_from_fulltext(xml_path.read_bytes())
            if (
                article["pmcid"] != pmcid
                or article["pmid"] != item["pmid"]
                or article["doi"].lower() != item["doi"].lower()
            ):
                raise ValueError(f"XML 与采集报告标识不一致：{pmcid}")
            authors = [entry["fullName"] for entry in article["authorList"]["author"]]
            if not authors:
                raise ValueError(f"XML 未能解析出作者：{pmcid}")
            expected[pmcid] = {
                "pmid": item["pmid"],
                "doi": item["doi"],
                "authors": authors,
                "xml_sha256": item["xml_sha256"],
            }
    return expected, sources


def verify(source: Path, target: Path, expected: dict[str, dict], report: dict) -> dict:
    errors = []
    with closing(open_readonly(source / "knowledge.sqlite3")) as parent:
        with closing(open_readonly(target / "knowledge.sqlite3")) as child:
            old_rows = read_documents(parent)
            new_rows = read_documents(child)
            if set(old_rows) != set(new_rows):
                errors.append("子版本文档集合与父库不同")
            changed = []
            for document_id in old_rows.keys() & new_rows.keys():
                old = old_rows[document_id]
                new = new_rows[document_id]
                if any(
                    old[key] != new[key]
                    for key in ("content_hash", "filename", "title", "text", "file_type", "byte_size")
                ):
                    errors.append(f"正文或原始文件字段改变：{document_id}")
                old_meta = json.loads(old["metadata"])
                new_meta = json.loads(new["metadata"])
                pmcid = old_meta.get("pmcid")
                if pmcid in expected and not old_meta.get("authors"):
                    if new_meta.get("authors") != expected[pmcid]["authors"]:
                        errors.append(f"作者与 XML 不一致：{pmcid}")
                    new_meta["authors"] = old_meta.get("authors")
                    changed.append(pmcid)
                if old_meta != new_meta:
                    errors.append(f"除作者外的元数据发生变化：{document_id}")
            if set(changed) != {entry["pmcid"] for entry in report["entries"]}:
                errors.append("补齐文献集合与变更报告不一致")
            for table in ("chunks", "embeddings"):
                before = parent.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                after = child.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if before != after:
                    errors.append(f"{table} 行数发生变化")
                if table_digest(parent, table) != table_digest(child, table):
                    errors.append(f"{table} 内容摘要发生变化")
            fingerprints = [
                tuple(row)
                for row in child.execute(
                    "SELECT fingerprint,dimensions,COUNT(*) FROM embeddings "
                    "GROUP BY fingerprint,dimensions"
                )
            ]
            if fingerprints != [
                tuple(row)
                for row in parent.execute(
                    "SELECT fingerprint,dimensions,COUNT(*) FROM embeddings "
                    "GROUP BY fingerprint,dimensions"
                )
            ]:
                errors.append("向量模型指纹或维度发生变化")
            if child.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                errors.append("子库 SQLite 完整性检查失败")
    source_uploads = {path.name: file_sha256(path) for path in (source / "uploads").glob("*")}
    target_uploads = {path.name: file_sha256(path) for path in (target / "uploads").glob("*")}
    if source_uploads != target_uploads:
        errors.append("上传原件与父版本不同")
    if file_sha256(source / "knowledge.sqlite3") != report["parent_database_sha256"]:
        errors.append("父库摘要发生变化")
    if file_sha256(target / "knowledge.sqlite3") != report["target_database_sha256"]:
        errors.append("子库摘要与变更报告不一致")
    result = {
        "verified_at_utc": utc_now(),
        "parent_database_sha256": file_sha256(source / "knowledge.sqlite3"),
        "target_database_sha256": file_sha256(target / "knowledge.sqlite3"),
        "enriched_papers": len(changed),
        "documents": len(new_rows),
        "vector_profiles": fingerprints,
        "errors": errors,
        "passed": not errors,
        "human_academic_review": "pending",
    }
    save_json_atomic(target / "AUTHOR_VERIFICATION.json", result)
    return result


def enrich(source: Path, target: Path, batches: list[Path]) -> dict:
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    batches = [batch.resolve(strict=True) for batch in batches]
    report_path = target / "AUTHOR_ENRICHMENT.json"
    if report_path.exists():
        raise FileExistsError("作者补齐报告已存在，不能重复写入")
    parent_hash = file_sha256(source / "knowledge.sqlite3")
    revision = json.loads((target / "REVISION.json").read_text(encoding="utf-8"))
    if revision["parent_database_sha256"] != parent_hash:
        raise ValueError("子版本的父库摘要与当前来源不一致")
    expected, sources = expected_authors(batches)
    with closing(open_readonly(source / "knowledge.sqlite3")) as parent:
        old_rows = read_documents(parent)
    with closing(sqlite3.connect(target / "knowledge.sqlite3")) as child:
        new_rows = read_documents(child)
        if set(old_rows) != set(new_rows) or any(
            old_rows[key]["metadata"] != new_rows[key]["metadata"] for key in old_rows
        ):
            raise ValueError("子库尚未保持父库文档元数据原样")
        entries = []
        child.execute("BEGIN IMMEDIATE")
        for document_id, row in new_rows.items():
            metadata = json.loads(row["metadata"])
            if metadata.get("content_type") != "fulltext" or metadata.get("authors"):
                continue
            pmcid = metadata.get("pmcid")
            record = expected.get(pmcid)
            if not record:
                raise ValueError(f"作者为空的全文缺少已登记原文：{pmcid}")
            if (
                str(metadata.get("pmid")) != record["pmid"]
                or str(metadata.get("doi", "")).lower() != record["doi"].lower()
                or metadata.get("xml_sha256") != record["xml_sha256"]
            ):
                raise ValueError(f"数据库与 XML 文献标识不一致：{pmcid}")
            metadata["authors"] = record["authors"]
            child.execute(
                "UPDATE documents SET metadata=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), document_id),
            )
            entries.append({
                "pmcid": pmcid,
                "document_id": document_id,
                "xml_sha256": record["xml_sha256"],
                "authors": record["authors"],
            })
        if not entries:
            raise ValueError("未发现可补齐作者的全文")
        child.execute("UPDATE app_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        child.commit()
        child.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    report = {
        "enriched_at_utc": utc_now(),
        "parent_database_sha256": parent_hash,
        "target_database_sha256": file_sha256(target / "knowledge.sqlite3"),
        "sources": sources,
        "entries": sorted(entries, key=lambda entry: entry["pmcid"]),
        "count": len(entries),
        "scope": "只补齐作者元数据；原始 XML、上传原件、正文和向量保持不变",
    }
    save_json_atomic(report_path, report)
    result = verify(source, target, expected, report)
    if not result["passed"]:
        raise ValueError("作者补齐后的独立版本核验失败")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        source = args.source.resolve(strict=True)
        target = args.target.resolve(strict=True)
        expected, sources = expected_authors([batch.resolve(strict=True) for batch in args.batch])
        report = json.loads((target / "AUTHOR_ENRICHMENT.json").read_text(encoding="utf-8"))
        if report["sources"] != sources:
            raise ValueError("采集报告摘要与作者补齐时不同")
        result = verify(source, target, expected, report)
    else:
        result = enrich(args.source, args.target, args.batch)
    print(json.dumps({key: result[key] for key in ("enriched_papers", "documents", "errors", "passed")}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
