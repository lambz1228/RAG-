"""将知识库复制为独立实验版本，原始数据库和归档保持只读。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .errors import KnowledgeError
from .quality import assess_document
from .storage import Repository


def create_revision(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    database = source / "knowledge.sqlite3"
    if not database.is_file() or target.exists() or target.is_relative_to(source):
        raise KnowledgeError("原数据库必须存在，目标必须是独立且尚不存在的目录")
    parent_digest = hashlib.sha256(database.read_bytes()).hexdigest()
    target.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target / "knowledge.sqlite3")) as destination:
            original.backup(destination)
    for name in ("uploads", "sources"):
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name)
    repository = Repository(target / "knowledge.sqlite3")
    entries = []
    with repository.connection() as conn:
        for row in conn.execute("SELECT id,text,metadata,content_hash FROM documents").fetchall():
            meta = json.loads(row["metadata"])
            meta["quality"] = assess_document(row["text"], meta)
            meta["content_type"] = meta["quality"]["content_type"]
            conn.execute(
                "UPDATE documents SET metadata=? WHERE id=?",
                (
                    json.dumps(meta, ensure_ascii=False),
                    row["id"],
                ),
            )
            entries.append(
                {
                    "document_id": row["id"],
                    "source_id": meta.get("source_id"),
                    "raw_sha256": row["content_hash"],
                    "quality": meta["quality"],
                }
            )
        repository._touch(conn)
        conn.execute(
            "INSERT OR REPLACE INTO app_meta VALUES ('parent_database_sha256',?)", (parent_digest,)
        )
    report = {
        "source": str(source),
        "target": str(target),
        "parent_database_sha256": parent_digest,
        "documents": len(entries),
        "eligible": sum(e["quality"]["eligible"] for e in entries),
        "excluded": sum(not e["quality"]["eligible"] for e in entries),
        "entries": entries,
    }
    report["corpus_sha256"] = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if hashlib.sha256(database.read_bytes()).hexdigest() != parent_digest:
        raise KnowledgeError("复制期间原库发生变化，目标未通过版本核验")
    (target / "REVISION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
