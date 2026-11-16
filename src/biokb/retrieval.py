"""BM25、向量相似度和倒数排名融合；返回可追溯原文片段。"""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, OrderedDict

from .config import Settings
from .errors import IndexMismatchError, KnowledgeError
from .papers import build_catalog, resolve_scope
from .providers import Embedder
from .quality import assess_document, matches_filters, validate_filters
from .reranking import Reranker
from .storage import Repository
from .text import TERM_EXPANSION_VERSION, expand_semantic_query, tokenize


def validate_query(query: str, top_k: int) -> str:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 4000:
        raise KnowledgeError("问题长度必须在 2–4000 个字符之间")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
        raise KnowledgeError("top_k 必须为 1–50 的整数")
    return query.strip()


class RetrievalService:
    def __init__(
        self, repository: Repository, embedder: Embedder, settings: Settings, reranker=None
    ):
        self.repository, self.embedder, self.settings = repository, embedder, settings
        self._lock = threading.RLock()
        self._revision = -1
        self._rows: list[dict] = []
        self._tokens: list[Counter] = []
        self._lengths: list[int] = []
        self._idf: dict[str, float] = {}
        self.reranker = reranker or Reranker(settings)
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()

    def _snapshot(self) -> tuple:
        with self._lock:
            revision = self.repository.revision
            if revision != self._revision:
                rows = self.repository.index_rows()
                for row in rows:
                    quality = row["metadata"].get("quality") or assess_document(
                        row["text"], row["metadata"]
                    )
                    row["metadata"] = {
                        **row["metadata"],
                        "quality": quality,
                        "content_type": quality["content_type"],
                    }
                rows = [row for row in rows if row["metadata"]["quality"]["eligible"]]
                tokens = [Counter(tokenize(row["title"] + " " + row["text"])) for row in rows]
                frequencies = Counter(term for item in tokens for term in item)
                self._rows, self._tokens = rows, tokens
                self._lengths = [sum(item.values()) for item in tokens]
                self._idf = {
                    term: math.log(1 + (len(rows) - count + 0.5) / (count + 0.5))
                    for term, count in frequencies.items()
                }
                self._revision = revision
            return self._rows, self._tokens, self._lengths, self._idf, self._revision

    def search(
        self,
        query: str,
        strategy: str | None = None,
        top_k: int | None = None,
        document_id: str = "",
        filters: dict | None = None,
        paper_ids: list[str] | None = None,
    ) -> dict:
        start = time.perf_counter()
        top_k = self.settings.top_k if top_k is None else top_k
        query = validate_query(query, top_k)
        strategy = strategy or self.settings.default_retrieval
        if not isinstance(strategy, str) or strategy not in {
            "bm25",
            "dense",
            "hybrid",
            "hybrid_rerank",
        }:
            raise KnowledgeError("检索方式必须为 bm25、dense、hybrid 或 hybrid_rerank")
        if not isinstance(document_id, str):
            raise KnowledgeError("document_id 必须为字符串")
        filters = validate_filters(filters)
        rows, tokens, lengths, idf, revision = self._snapshot()
        catalog = build_catalog(self.repository.document_catalog())
        scope = resolve_scope(self.repository, paper_ids, catalog)
        allowed_documents = set(scope["document_ids"])
        paper_by_document = {
            document: paper["id"]
            for paper in catalog["items"]
            for document in paper["document_ids"]
        }
        indices = [
            i
            for i, row in enumerate(rows)
            if (not document_id or row["document_id"] == document_id)
            and (scope["kind"] == "library" or row["document_id"] in allowed_documents)
            and matches_filters(row["metadata"], filters)
        ]
        query_tokens = set(tokenize(query))
        encoded_query, expansion_terms = (
            expand_semantic_query(query) if self.settings.query_term_expansion else (query, [])
        )
        if not query_tokens:
            raise KnowledgeError("问题中没有可检索的文字")
        bm25: dict[int, float] = {}
        avg_length = sum(lengths) / max(len(lengths), 1)
        for i in indices:
            score = 0.0
            for term in query_tokens:
                frequency = tokens[i].get(term, 0)
                if frequency:
                    denominator = frequency + 1.5 * (0.25 + 0.75 * lengths[i] / max(avg_length, 1))
                    score += idf.get(term, 0) * frequency * 2.5 / denominator
            if score > 0:
                bm25[i] = score
        dense: dict[int, float] = {}
        warnings = []
        if strategy in {"dense", "hybrid", "hybrid_rerank"} and indices:
            if any(rows[i]["fingerprint"] != self.embedder.fingerprint for i in indices):
                raise IndexMismatchError(
                    "当前向量模型与已存索引不一致，请先重建向量索引；也可使用 BM25 检索"
                )
            with self._lock:
                query_vector = self._query_cache.get(encoded_query)
                if query_vector is not None:
                    self._query_cache.move_to_end(encoded_query)
            if query_vector is None:
                encode_query = getattr(self.embedder, "embed_query", None)
                query_vector = (
                    encode_query(encoded_query)
                    if encode_query
                    else self.embedder.embed_batch([encoded_query])[0]
                )
                with self._lock:
                    self._query_cache[encoded_query] = query_vector
                    if len(self._query_cache) > 128:
                        self._query_cache.popitem(last=False)
            if any(len(rows[i]["vector"]) != len(query_vector) for i in indices):
                raise IndexMismatchError("查询向量与文献向量维度不一致，请重建索引")
            for i in indices:
                # 哈希基线可能发生碰撞，必须同时具有词项重合才召回。
                if self.settings.embedding_backend == "hash" and i not in bm25:
                    continue
                score = sum(a * b for a, b in zip(query_vector, rows[i]["vector"]))
                if score >= self.settings.dense_min_score:
                    dense[i] = score
        if self.settings.embedding_backend == "hash" and strategy != "bm25":
            warnings.append(
                "当前向量为离线哈希基线，跨语言和语义检索能力有限；正式语义实验需配置向量模型"
            )
        if strategy == "bm25":
            scores = bm25
        elif strategy == "dense":
            scores = dense
        else:
            scores = {}
            for ranking in (bm25, dense):
                for rank, (index, _) in enumerate(
                    sorted(ranking.items(), key=lambda item: (-item[1], rows[item[0]]["id"]))[
                        : max(self.settings.retrieval_candidates, top_k)
                    ],
                    1,
                ):
                    scores[index] = scores.get(index, 0) + 1 / (self.settings.rrf_constant + rank)
        hits = []
        for index, score in sorted(
            scores.items(), key=lambda item: (-item[1], rows[item[0]]["id"])
        )[
            : max(top_k, self.settings.retrieval_candidates)
            if strategy == "hybrid_rerank"
            else top_k
        ]:
            row = rows[index]
            hits.append(
                {
                    "chunk_id": row["id"],
                    "document_id": row["document_id"],
                    "paper_id": paper_by_document.get(row["document_id"], ""),
                    "title": row["title"],
                    "filename": row["filename"],
                    "text": row["text"],
                    "locator": row["locator"],
                    "start_offset": row["start_offset"],
                    "end_offset": row["end_offset"],
                    "metadata": row["metadata"],
                    "score": round(score, 6),
                    "bm25_score": round(bm25.get(index, 0), 6),
                    "dense_score": round(dense.get(index, 0), 6),
                }
            )
        if strategy == "hybrid_rerank":
            hits = self.reranker.rank(query, hits)[:top_k]
        return {
            "query": query,
            "query_expansion": {
                "version": TERM_EXPANSION_VERSION
                if self.settings.query_term_expansion and strategy != "bm25"
                else "",
                "terms": expansion_terms if strategy != "bm25" else [],
            },
            "strategy": strategy,
            "hits": hits,
            "warnings": warnings,
            "index_revision": revision,
            "embedding_fingerprint": self.embedder.fingerprint,
            "reranker_fingerprint": self.reranker.fingerprint
            if strategy == "hybrid_rerank"
            else "",
            "filters": filters,
            "scope": {**scope, "document_id": document_id, "filters": filters},
            "parameters": {
                "candidate_chunks": self.settings.retrieval_candidates,
                "rrf_constant": self.settings.rrf_constant,
                "dense_min_score": self.settings.dense_min_score,
            },
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
