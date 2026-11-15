"""只读统计当前生物文献库的论文版本、来源和复核状态。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.papers import build_catalog  # noqa: E402


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(database: Path) -> dict:
    database = database.resolve(strict=True)
    before = file_sha256(database)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        records = [
            {
                "id": row[0],
                "title": row[1],
                "filename": row[2],
                "metadata": json.loads(row[3]),
            }
            for row in connection.execute("SELECT id,title,filename,metadata FROM documents")
        ]
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "chunks", "embeddings")
        }
        revision = connection.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[
            0
        ]
        embeddings = {
            fingerprint: total
            for fingerprint, total in connection.execute(
                "SELECT fingerprint, COUNT(*) FROM embeddings GROUP BY fingerprint"
            )
        }
        connection.rollback()
    catalog = build_catalog(records)
    quality = Counter()
    types = Counter()
    sources = Counter()
    for record in records:
        metadata = record["metadata"]
        quality[str((metadata.get("quality") or {}).get("status") or "未记录")] += 1
        types[str(metadata.get("content_type") or "未记录")] += 1
        sources[str(metadata.get("source") or "未记录")] += 1
    after = file_sha256(database)
    return {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_unchanged": before == after,
        "index_revision": revision,
        "embedding_fingerprints": embeddings,
        "counts": counts,
        "content_types": dict(sorted(types.items())),
        "sources": dict(sorted(sources.items())),
        "quality_status": dict(sorted(quality.items())),
        "paper_summary": catalog["summary"],
        "paper_version_groups": [
            {"paper_id": paper["id"], "document_ids": paper["document_ids"]}
            for paper in catalog["items"]
            if len(paper["document_ids"]) > 1
        ],
        "limits": [
            "目录合并仅依据当前元数据中的正式编号，不验证论文全文是否支持具体论断",
            "质量状态来自已有自动标注，不能视为人工审核结论",
            "数据库文件摘要不包含可能同时存在的 SQLite WAL 文件内容",
        ],
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.database.resolve():
        parser.error("报告路径不能覆盖被审计的数据库")
    report = audit(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"source_unchanged": report["source_unchanged"], **report["paper_summary"]},
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["source_unchanged"] else 1)
