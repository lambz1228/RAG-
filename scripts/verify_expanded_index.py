"""只读核对扩充版知识库、采集原件、元数据与语义向量。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from biokb.text import normalize_text
from scripts.expand_open_corpus import file_sha256, save_json_atomic, utc_now, verify_batch


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def paper_pmids(rows: dict[str, sqlite3.Row]) -> set[str]:
    result = set()
    for row in rows.values():
        meta = json.loads(row["metadata"])
        pmid = str(meta.get("pmid") or meta.get("id") or "")
        if pmid.isdigit():
            result.add(pmid)
    return result


def verify(batch: Path, target: Path) -> dict:
    batch = batch.resolve(strict=True)
    target = target.resolve(strict=True)
    acquisition = json.loads((batch / "acquisition_report.json").read_text(encoding="utf-8"))
    curation = json.loads((batch / "curation_report.json").read_text(encoding="utf-8"))
    imported = json.loads((batch / "import_report.json").read_text(encoding="utf-8"))
    parent_db = Path(acquisition["database"])
    target_db = target / "knowledge.sqlite3"
    included = [entry for entry in curation["entries"] if entry["included"]]
    excluded = [entry for entry in curation["entries"] if not entry["included"]]
    errors = []
    try:
        verify_batch(acquisition, parent_db, batch / "raw_xml", batch / "documents")
    except ValueError as exc:
        errors.append(f"采集原件或父库核验失败：{exc}")
    if file_sha256(batch / "acquisition_report.json") != curation[
        "source_acquisition_report_sha256"
    ]:
        errors.append("采集报告与筛选时的摘要不一致")
    if acquisition.get("metadata_source") == "europe_pmc_fulltext_xml":
        audit = json.loads((batch / "pubmed_snapshot_audit.json").read_text(encoding="utf-8"))
        if not audit["passed"] or audit["pubmed_xml_sha256"] != file_sha256(
            batch / "pubmed_metadata.xml"
        ):
            errors.append("PubMed 原始 XML 重新核验未通过")
    if file_sha256(parent_db) != curation["source_database_sha256"]:
        errors.append("父库文件摘要与筛选时不同")
    if imported["counts"]["imported"] != len(included):
        errors.append("入库清单尚未覆盖全部筛选文献")
    if imported["embedding_fingerprint"] != imported["counts"]["database"]["index_fingerprints"][0]:
        errors.append("入库记录的向量指纹与数据库统计不一致")
    with closing(open_readonly(parent_db)) as source, closing(open_readonly(target_db)) as database:
        old_rows = {
            row["id"]: row
            for row in source.execute(
                "SELECT id,content_hash,filename,title,text,metadata FROM documents"
            )
        }
        all_rows = {
            row["id"]: row
            for row in database.execute(
                "SELECT id,content_hash,filename,title,text,metadata FROM documents"
            )
        }
        for document_id, original in old_rows.items():
            current = all_rows.get(document_id)
            if current is None or any(
                current[key] != original[key]
                for key in ("content_hash", "filename", "title", "text", "metadata")
            ):
                errors.append(f"父库文档发生缺失或变化：{document_id}")
        if len(all_rows) != len(old_rows) + len(included):
            errors.append("新库文档数与父库加筛选文献数不一致")

        new_documents = []
        for entry in included:
            path = batch / "curated_documents" / f"{entry['pmcid']}.json"
            if file_sha256(path) != entry["curated_json_sha256"]:
                errors.append(f"筛选文件摘要不一致：{entry['pmcid']}")
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            matches = [
                row
                for row in all_rows.values()
                if row["content_hash"] == entry["curated_json_sha256"]
            ]
            if len(matches) != 1:
                errors.append(f"入库文件缺失或重复：{entry['pmcid']}")
                continue
            row = matches[0]
            meta = json.loads(row["metadata"])
            if (
                row["text"] != normalize_text(payload["content"])
                or row["title"] != payload["title"]
                or meta.get("source_id") != payload["source_id"]
                or meta.get("pmid") != entry["pmid"]
                or meta.get("pmcid") != entry["pmcid"]
                or meta.get("doi") != entry["doi"]
                or meta.get("license") != entry["license_url"]
                or meta.get("xml_sha256") != entry["source_xml_sha256"]
                or meta.get("publication_types") != entry["publication_types"]
                or meta.get("content_type") != "fulltext"
                or meta.get("quality", {}).get("status") != "unreviewed"
            ):
                errors.append(f"入库正文或元数据不一致：{entry['pmcid']}")
            upload = target / "uploads" / (entry["curated_json_sha256"] + ".json")
            if not upload.is_file() or file_sha256(upload) != entry["curated_json_sha256"]:
                errors.append(f"入库原文件摘要不一致：{entry['pmcid']}")
            chunks = database.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id=?", (row["id"],)
            ).fetchone()[0]
            vectors = database.execute(
                "SELECT COUNT(*) FROM embeddings e JOIN chunks c ON e.chunk_id=c.id "
                "WHERE c.document_id=? AND e.fingerprint=?",
                (row["id"], imported["embedding_fingerprint"]),
            ).fetchone()[0]
            if chunks < 1 or vectors != chunks:
                errors.append(f"文档分块和语义向量不一致：{entry['pmcid']}")
            new_documents.append(
                {"pmcid": entry["pmcid"], "document_id": row["id"], "chunks": chunks}
            )

        for entry in excluded:
            if any(row["content_hash"] == entry["source_json_sha256"] for row in all_rows.values()):
                errors.append(f"排除文献仍出现在新索引：{entry['pmcid']}")
        total_chunks = database.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        total_vectors = database.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        index_fingerprints = database.execute(
            "SELECT fingerprint,dimensions,COUNT(*) FROM embeddings GROUP BY fingerprint,dimensions"
        ).fetchall()
        if total_chunks != total_vectors or len(index_fingerprints) != 1:
            errors.append("索引存在缺失向量或混合模型指纹/维度")
        if (
            index_fingerprints
            and index_fingerprints[0]["fingerprint"] != imported["embedding_fingerprint"]
        ):
            errors.append("数据库向量指纹与入库报告不一致")
        old_papers = paper_pmids(old_rows)
        new_papers = paper_pmids(all_rows)
        if len(new_papers - old_papers) != curation["counts"]["new_papers"]:
            errors.append("新增论文去重后的数量与筛选报告不一致")
        content_types = Counter(
            json.loads(row["metadata"]).get("content_type") for row in all_rows.values()
        )
    result = {
        "verified_at_utc": utc_now(),
        "parent_database_sha256": file_sha256(parent_db),
        "target_database_sha256": file_sha256(target_db),
        "acquisition_report_sha256": file_sha256(batch / "acquisition_report.json"),
        "curation_report_sha256": file_sha256(batch / "curation_report.json"),
        "import_report_sha256": file_sha256(batch / "import_report.json"),
        "counts": {
            "parent_documents": len(old_rows),
            "target_documents": len(all_rows),
            "new_fulltext_records": len(new_documents),
            "old_unique_papers": len(old_papers),
            "target_unique_papers": len(new_papers),
            "new_unique_papers": len(new_papers - old_papers),
            "chunks": total_chunks,
            "vectors": total_vectors,
            "content_types": dict(content_types),
            "vector_profiles": [
                {
                    "fingerprint": row["fingerprint"],
                    "dimensions": row["dimensions"],
                    "count": row[2],
                }
                for row in index_fingerprints
            ],
        },
        "new_documents": new_documents,
        "errors": errors,
        "passed": not errors,
        "human_academic_review": "pending",
    }
    save_json_atomic(batch / "index_verification.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.batch, args.target)
    print(
        json.dumps(
            {"counts": result["counts"], "errors": result["errors"]}, ensure_ascii=False, indent=2
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
