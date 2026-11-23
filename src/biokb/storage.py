"""SQLite 持久化；每个操作独立连接，文档与索引事务性更新。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import ConflictError, NotFoundError
from .quality import assess_document, matches_filters, validate_filters


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL, title TEXT NOT NULL,
                    text TEXT NOT NULL, metadata TEXT NOT NULL,
                    file_type TEXT NOT NULL, byte_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL,
                    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, locator TEXT NOT NULL,
                    UNIQUE(document_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, dimensions INTEGER NOT NULL, vector TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS query_runs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, query TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO app_meta VALUES ('schema_version', '2.0');
                INSERT OR IGNORE INTO app_meta VALUES ('revision', '0');
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_runs_created ON query_runs(created_at);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新对话',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, run_id TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _touch(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE app_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    @property
    def revision(self) -> int:
        with self.connection() as conn:
            return int(
                conn.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[0]
            )

    def find_by_hash(self, digest: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id,title FROM documents WHERE content_hash=?", (digest,)
            ).fetchone()
            return dict(row) if row else None

    def insert_document(
        self, document: dict, chunks: list[dict], vectors: list[list[float]], fingerprint: str
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("分块数与向量数不一致")
        try:
            with self.connection() as conn:
                conn.execute(
                    "INSERT INTO documents (id,content_hash,filename,title,text,metadata,file_type,byte_size) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        document["id"],
                        document["content_hash"],
                        document["filename"],
                        document["title"],
                        document["text"],
                        json.dumps(document["metadata"], ensure_ascii=False),
                        document["file_type"],
                        document["byte_size"],
                    ),
                )
                for chunk, vector in zip(chunks, vectors):
                    conn.execute(
                        "INSERT INTO chunks VALUES (?,?,?,?,?,?,?)",
                        (
                            chunk["id"],
                            document["id"],
                            chunk["ordinal"],
                            chunk["text"],
                            chunk["start"],
                            chunk["end"],
                            chunk["locator"],
                        ),
                    )
                    conn.execute(
                        "INSERT INTO embeddings VALUES (?,?,?,?)",
                        (chunk["id"], fingerprint, len(vector), json.dumps(vector)),
                    )
                self._touch(conn)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该文档已入库，请刷新文献列表") from exc

    def list_documents(
        self, query: str = "", limit: int = 50, offset: int = 0, filters: dict | None = None
    ) -> dict:
        filters = validate_filters(filters)
        term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT d.id,d.title,d.filename,d.file_type,d.byte_size,d.metadata,d.created_at,
                (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count
                FROM documents d WHERE d.title LIKE ? ESCAPE '\\' OR d.filename LIKE ? ESCAPE '\\'
                ORDER BY d.created_at DESC,d.id""",
                (term, term),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item["metadata"])
                if matches_filters(item["metadata"], filters):
                    items.append(item)
            return {
                "items": items[offset : offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
            }

    def document(
        self, document_id: str, include_chunks: bool = True, include_text: bool = True
    ) -> dict:
        with self.connection() as conn:
            fields = (
                "*"
                if include_text
                else "id,content_hash,filename,title,metadata,file_type,byte_size,created_at"
            )
            row = conn.execute(
                f"SELECT {fields} FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("文档不存在")
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            if include_chunks:
                item["chunks"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id,ordinal,text,start_offset,end_offset,locator FROM chunks WHERE document_id=? ORDER BY ordinal",
                        (document_id,),
                    )
                ]
            return item

    def document_catalog(self) -> list[dict]:
        """只读取目录字段，避免论文目录加载完整正文与向量。"""
        with self.connection() as conn:
            return [
                {**dict(row), "metadata": json.loads(row["metadata"])}
                for row in conn.execute(
                    "SELECT id,title,filename,metadata FROM documents ORDER BY id"
                )
            ]

    def evidence_window(self, hit: dict, neighbors: int) -> dict:
        """按保存的原文偏移合并相邻分块，保留召回中心及扩展范围。"""
        if not neighbors:
            return hit
        doc = self.document(hit["document_id"])
        chunks = doc["chunks"]
        index = next(i for i, chunk in enumerate(chunks) if chunk["id"] == hit["chunk_id"])
        selected = chunks[max(0, index - neighbors) : index + neighbors + 1]
        start, end = selected[0]["start_offset"], selected[-1]["end_offset"]
        return {
            **hit,
            "text": doc["text"][start:end],
            "start_offset": start,
            "end_offset": end,
            "locator": f"字符 {start + 1}–{end}",
            "retrieved_locator": hit["locator"],
            "retrieved_start_offset": hit["start_offset"],
            "retrieved_end_offset": hit["end_offset"],
            "context_chunk_ids": [chunk["id"] for chunk in selected],
        }

    def delete_document(self, document_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT content_hash,file_type FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("文档不存在")
            conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
            self._touch(conn)
            return dict(row)

    def index_rows(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("""SELECT c.*,d.title,d.filename,d.metadata,e.fingerprint,e.dimensions,e.vector
                FROM chunks c JOIN documents d ON d.id=c.document_id LEFT JOIN embeddings e ON e.chunk_id=c.id
                ORDER BY c.document_id,c.ordinal""").fetchall()
            return [
                {
                    **dict(r),
                    "metadata": json.loads(r["metadata"]),
                    "vector": json.loads(r["vector"]) if r["vector"] else [],
                }
                for r in rows
            ]

    def replace_vectors(
        self, rows: list[tuple[str, list[float]]], fingerprint: str, expected_revision: int
    ) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            revision = int(
                conn.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[0]
            )
            if revision != expected_revision:
                raise ConflictError("索引期间文献发生变化，请重新执行索引")
            for chunk_id, vector in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                    (chunk_id, fingerprint, len(vector), json.dumps(vector)),
                )
            self._touch(conn)

    def save_run(self, run_id: str, kind: str, query: str, result: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO query_runs (id,kind,query,result) VALUES (?,?,?,?)",
                (run_id, kind, query, json.dumps(result, ensure_ascii=False)),
            )

    def history(self, limit: int = 30, kind: str = "answer") -> list[dict]:
        with self.connection() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id,kind,query,created_at FROM query_runs WHERE kind=? ORDER BY created_at DESC LIMIT ?",
                    (kind, limit),
                )
            ]

    def run(self, run_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT result FROM query_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError("问答记录不存在")
            return json.loads(row[0])

    # ---- 会话（多轮问答）：会话仅保存问题与回答文本，证据仍以问答运行记录为准 ----

    def create_conversation(self, title: str = "") -> dict:
        conversation_id = "conv_" + uuid.uuid4().hex
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id,title) VALUES (?,?)",
                (conversation_id, (title or "新对话")[:80]),
            )
        return self.get_conversation(conversation_id)

    def get_conversation(self, conversation_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT c.id,c.title,c.created_at,c.updated_at,
                (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id=c.id) AS message_count
                FROM conversations c WHERE c.id=?""",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("对话不存在")
            return dict(row)

    def list_conversations(self, limit: int = 50) -> list[dict]:
        with self.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT c.id,c.title,c.created_at,c.updated_at,
                    (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id=c.id) AS message_count
                    FROM conversations c ORDER BY c.updated_at DESC,c.id LIMIT ?""",
                    (limit,),
                )
            ]

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        """写入一条消息；message_id 相同则跳过，供任务重试时幂等去重。"""
        if role not in {"user", "assistant"}:
            raise ValueError("消息角色必须是 user 或 assistant")
        message_id = message_id or "msg_" + uuid.uuid4().hex
        with self.connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
                ).fetchone()
                is None
            ):
                raise NotFoundError("对话不存在")
            cursor = conn.execute(
                "INSERT OR IGNORE INTO conversation_messages (id,conversation_id,role,content,run_id) VALUES (?,?,?,?,?)",
                (message_id, conversation_id, role, str(content)[:16000], run_id),
            )
            if not cursor.rowcount:
                return None
            conn.execute(
                "UPDATE conversations SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (conversation_id,),
            )
            # 首个用户提问自动作为会话标题，方便会话列表辨认。
            if role == "user":
                conn.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND title='新对话'",
                    (str(content).strip()[:80] or "新对话", conversation_id),
                )
            row = conn.execute(
                "SELECT * FROM conversation_messages WHERE id=?", (message_id,)
            ).fetchone()
            return dict(row)

    def conversation_messages(self, conversation_id: str) -> list[dict]:
        self.get_conversation(conversation_id)
        with self.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id,role,content,run_id,created_at FROM conversation_messages WHERE conversation_id=? ORDER BY rowid",
                    (conversation_id,),
                )
            ]

    def conversation_history(self, conversation_id: str, limit: int = 3) -> list[dict]:
        """最近若干完整问答轮次，供生成模型结合会话记忆；未完成的轮次不计入。"""
        messages = self.conversation_messages(conversation_id)
        pairs: list[dict] = []
        pending: dict | None = None
        for message in reversed(messages):
            if message["role"] == "assistant" and pending is None:
                pending = message
            elif message["role"] == "user" and pending is not None:
                pairs.append({"question": message["content"], "answer": pending["content"]})
                pending = None
                if len(pairs) >= limit:
                    break
        pairs.reverse()
        return pairs

    def delete_conversation(self, conversation_id: str) -> None:
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            if not cursor.rowcount:
                raise NotFoundError("对话不存在")

    def stats(self) -> dict:
        with self.connection() as conn:
            return {
                "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "vectors": conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
                "questions": conn.execute(
                    "SELECT COUNT(*) FROM query_runs WHERE kind='answer'"
                ).fetchone()[0],
                "index_fingerprints": [
                    r[0] for r in conn.execute("SELECT DISTINCT fingerprint FROM embeddings")
                ],
            }

    def eligible_source_ids(self) -> set[str]:
        with self.connection() as conn:
            result = set()
            for row in conn.execute("SELECT id,text,metadata FROM documents"):
                meta = json.loads(row["metadata"])
                quality = meta.get("quality") or assess_document(row["text"], meta)
                if quality["eligible"]:
                    result.add(str(meta.get("source_id") or row["id"]))
            return result

    def facets(self) -> dict:
        values = {key: set() for key in ("topic", "year", "content_type")}
        with self.connection() as conn:
            for row in conn.execute("SELECT metadata FROM documents"):
                meta = json.loads(row[0])
                for key in values:
                    if meta.get(key):
                        values[key].add(str(meta[key]))
        return {key: sorted(items) for key, items in values.items()}

    def save_job(self, job: dict) -> None:
        import time

        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?,?)",
                (
                    job["id"],
                    json.dumps(job, ensure_ascii=False),
                    time.time(),
                ),
            )

    def load_jobs(self) -> list[dict]:
        with self.connection() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload FROM jobs ORDER BY updated_at DESC LIMIT 100"
                )
            ]

    def prune_jobs(self, keep: set[str]) -> None:
        with self.connection() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM jobs")]
            conn.executemany(
                "DELETE FROM jobs WHERE id=?", [(value,) for value in ids if value not in keep]
            )

    def corpus_manifest(self) -> dict:
        import hashlib

        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,content_hash,text,metadata FROM documents ORDER BY id"
            ).fetchall()
        entries = []
        for row in rows:
            meta = json.loads(row["metadata"])
            entries.append(
                {
                    "id": row["id"],
                    "raw_sha256": row["content_hash"],
                    "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
                    "metadata": meta,
                }
            )
        encoded = json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()
        return {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "documents": len(entries),
            "eligible_documents": len(self.eligible_source_ids()),
        }
