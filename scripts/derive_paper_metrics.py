"""只读复算冻结检索报告的论文级指标；不调用模型或改写历史报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.evaluation import derive_paper_metrics  # noqa: E402
from biokb.papers import build_catalog  # noqa: E402
from biokb.quality import assess_document  # noqa: E402


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(database: Path) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT id,title,filename,content_hash,text,metadata FROM documents ORDER BY id"
        ).fetchall()
        revision = int(
            conn.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[0]
        )
        fingerprints = {
            row[0] for row in conn.execute("SELECT DISTINCT fingerprint FROM embeddings")
        }
        conn.rollback()
    records = []
    entries = []
    sources = set()
    for document_id, title, filename, content_hash, text, raw_metadata in rows:
        metadata = json.loads(raw_metadata)
        records.append(
            {"id": document_id, "title": title, "filename": filename, "metadata": metadata}
        )
        entries.append(
            {
                "id": document_id,
                "raw_sha256": content_hash,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "metadata": metadata,
            }
        )
        quality = metadata.get("quality") or assess_document(text, metadata)
        if quality["eligible"]:
            sources.add(str(metadata.get("source_id") or document_id))
    catalog = build_catalog(records)
    by_document = {record["id"]: record for record in records}
    source_to_paper = {}
    for paper in catalog["items"]:
        for document_id in paper["document_ids"]:
            metadata = by_document[document_id]["metadata"]
            source = str(metadata.get("source_id") or document_id)
            if source in source_to_paper and source_to_paper[source] != paper["id"]:
                raise ValueError(f"同一 source_id 对应不同论文：{source}")
            source_to_paper[source] = paper["id"]
    manifest = {
        "sha256": hashlib.sha256(
            json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "documents": len(entries),
        "eligible_documents": len(sources),
    }
    return {
        "manifest": manifest,
        "index_revision": revision,
        "embedding_fingerprints": sorted(fingerprints),
        "source_to_paper": source_to_paper,
        "paper_summary": catalog["summary"],
    }


def run(report_path: Path, database: Path, output: Path) -> dict:
    report_path = report_path.resolve(strict=True)
    database = database.resolve(strict=True)
    output = output.resolve()
    if output in {report_path, database}:
        raise ValueError("输出路径不能覆盖原始检索报告或数据库")
    original_bytes = report_path.read_bytes()
    original = json.loads(original_bytes)
    before = file_sha256(database)
    current = snapshot(database)
    if original.get("corpus") != current["manifest"]:
        raise ValueError("冻结报告与当前数据库的语料摘要不一致")
    if original.get("index_revision") != current["index_revision"]:
        raise ValueError("冻结报告与当前数据库的索引修订号不一致")
    if original.get("embedding_fingerprint") not in current["embedding_fingerprints"]:
        raise ValueError("冻结报告与当前数据库的向量指纹不一致")
    derived = derive_paper_metrics(original, current["source_to_paper"])
    after = file_sha256(database)
    if before != after:
        raise ValueError("复算期间数据库主文件发生变化")
    derived.update(
        source_report=str(report_path),
        source_report_sha256=hashlib.sha256(original_bytes).hexdigest(),
        database=str(database),
        database_sha256_before=before,
        database_sha256_after=after,
        paper_summary=current["paper_summary"],
        source_to_paper=current["source_to_paper"],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
    return derived


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.report, args.database, args.output)
    print(
        json.dumps(
            {
                "dataset_sha256": result["dataset_sha256"],
                "paper_summary": result["paper_summary"],
                "strategies": {
                    method: {key: value for key, value in metrics.items() if key.startswith("Hit@")}
                    for method, metrics in result["results"].items()
                },
            },
            ensure_ascii=False,
        )
    )
