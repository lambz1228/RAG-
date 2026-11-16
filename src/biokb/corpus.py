"""冻结运行库及原始来源清单；验证源文件摘要，不触碰已有冻结目录。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .errors import KnowledgeError


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_corpus(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    original = source / "knowledge.sqlite3"
    if not original.is_file() or target.exists() or target.is_relative_to(source):
        raise KnowledgeError("需要已有运行库和独立、尚不存在的冻结目录")
    before = sha256(original)
    target.mkdir(parents=True, exist_ok=False)
    database = target / "knowledge.sqlite3"
    with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as conn:
        with closing(sqlite3.connect(database)) as copied:
            conn.backup(copied)
    for name in ("uploads", "sources"):
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        documents = [
            {
                "id": row[0],
                "title": row[1],
                "text_sha256": hashlib.sha256(row[2].encode()).hexdigest(),
                "metadata": json.loads(row[3]),
            }
            for row in conn.execute("SELECT id,title,text,metadata FROM documents ORDER BY id")
        ]
        profiles = [
            {"fingerprint": row[0], "dimensions": row[1], "count": row[2]}
            for row in conn.execute(
                "SELECT fingerprint,dimensions,count(*) FROM embeddings GROUP BY fingerprint,dimensions"
            )
        ]
        revision = int(
            conn.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[0]
        )
    if sha256(original) != before:
        raise KnowledgeError("冻结期间源库发生变化，请停用写入后重新冻结")
    files = {
        path.relative_to(target).as_posix(): sha256(path)
        for path in sorted(target.rglob("*"))
        if path.is_file() and not path.name.endswith(("-shm", "-wal"))
    }
    manifest = {
        "schema_version": 1,
        "scope": "开发语料冻结；来源与文件一致性不等于领域正确性",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_database_sha256": before,
        "database_sha256": files["knowledge.sqlite3"],
        "index_revision": revision,
        "documents": documents,
        "vector_profiles": profiles,
        "files": files,
    }
    (target / "CORPUS_MANIFEST.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return manifest


def verify_corpus(target: Path) -> dict:
    manifest = json.loads((target / "CORPUS_MANIFEST.json").read_text(encoding="utf-8"))
    errors = []
    for name, expected in manifest["files"].items():
        path = target / name
        if Path(name).is_absolute() or ".." in Path(name).parts or not path.is_file():
            errors.append("缺失或越界：" + name)
        elif sha256(path) != expected:
            errors.append("摘要不符：" + name)
    return {
        "passed": not errors,
        "files": len(manifest["files"]),
        "documents": len(manifest["documents"]),
        "errors": errors,
    }
