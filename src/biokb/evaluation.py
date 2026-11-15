"""独立参考答案的检索评测，兼容项目既有单出处和多出处数据。"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import ConflictError, KnowledgeError
from .retrieval import RetrievalService


def evaluate(
    retrieval: RetrievalService,
    dataset: dict,
    strategies: list[str] | None = None,
    k: int = 10,
    source_map: dict[str, str] | None = None,
) -> dict:
    if not isinstance(dataset, dict):
        raise KnowledgeError("评估集必须为 JSON 对象")
    items = dataset.get("items", [])
    if not isinstance(items, list) or not items:
        raise KnowledgeError("评估集需要非空 items 数组")
    if type(k) is not int or not 1 <= k <= 50:
        raise KnowledgeError("评估 k 必须在 1–50 之间")
    methods = strategies or ["bm25", "dense", "hybrid"]
    if not isinstance(methods, list) or any(
        not isinstance(method, str) or method not in {"bm25", "dense", "hybrid", "hybrid_rerank"}
        for method in methods
    ):
        raise KnowledgeError("评估检索方式不正确")
    revision = retrieval.repository.revision
    corpus = retrieval.repository.corpus_manifest()
    aliases = source_map if source_map is not None else dataset.get("source_map", {})
    if not isinstance(aliases, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not key or not value
        for key, value in aliases.items()
    ):
        raise KnowledgeError("source_map 必须为原编号到库内 source_id 的字符串映射")
    sources = retrieval.repository.eligible_source_ids()
    if any(
        target not in sources or (alias in sources and alias != target)
        for alias, target in aliases.items()
    ):
        raise KnowledgeError("出处映射目标不在知识库中，或试图覆盖已有 source_id")
    qids = set()
    required_sources = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            raise KnowledgeError(f"评估题 {index + 1} 需要 question 字符串")
        qid = str(item.get("qid", index + 1))
        if qid in qids:
            raise KnowledgeError("评估集存在重复 qid")
        qids.add(qid)
        golds = item.get("gold_doc_ids") or [item.get("gold_doc_id")]
        if (
            not isinstance(golds, list)
            or not golds
            or any(not isinstance(g, str) or not g for g in golds)
        ):
            raise KnowledgeError(f"评估题 {index + 1} 缺少有效的标准出处")
        if item.get("keep") is not False:
            required_sources.update(aliases.get(gold, gold) for gold in golds)
    missing = sorted(required_sources - sources)
    if missing:
        raise KnowledgeError(
            f"评估集中有 {len(missing)} 个标准出处不在知识库中，请导入对应文献或提供经核验的 source_map；首个缺失编号：{missing[0]}"
        )
    results = {}
    for strategy in methods:
        details = []
        latencies = []
        started = time.perf_counter()
        for index, item in enumerate(items):
            if item.get("keep") is False:
                continue
            golds = item.get("gold_doc_ids") or [item.get("gold_doc_id")]
            gold = {aliases.get(value, value) for value in golds if value}
            if not gold:
                raise KnowledgeError(f"评估题 {index + 1} 缺少标准出处")
            result = retrieval.search(item.get("question", ""), strategy, top_k=min(50, k * 5))
            latencies.append(result["latency_ms"])
            if result["index_revision"] != revision:
                raise ConflictError("评估期间知识库发生变化，请在文献及索引稳定后重新评估")
            ranked = []
            for hit in result["hits"]:
                source_id = str(hit["metadata"].get("source_id") or hit["document_id"])
                if source_id not in ranked:
                    ranked.append(source_id)
            top = ranked[:k]
            positions = [rank + 1 for rank, doc_id in enumerate(top) if doc_id in gold]
            matched = len(gold.intersection(top))
            details.append(
                {
                    "qid": item.get("qid", str(index + 1)),
                    "question": item["question"],
                    "ranked": top,
                    "gold": sorted(gold),
                    "original_gold": sorted(set(golds)),
                    "hit": bool(matched),
                    "recall": matched / len(gold),
                    "all_hit": matched == len(gold),
                    "reciprocal_rank": 1 / min(positions) if positions else 0.0,
                    "precision": matched / k,
                    "ndcg": sum(1 / math.log2(p + 1) for p in positions)
                    / sum(1 / math.log2(p + 1) for p in range(1, min(len(gold), k) + 1)),
                    "latency_ms": result["latency_ms"],
                    "failure_category": "none"
                    if matched == len(gold)
                    else "partial_coverage"
                    if matched
                    else "no_candidates"
                    if not top
                    else "gold_not_in_top_k",
                    "evidence": [
                        {
                            "source_id": hit["metadata"].get("source_id", hit["document_id"]),
                            "chunk_id": hit["chunk_id"],
                            "score": hit["score"],
                            "rerank_score": hit.get("rerank_score"),
                        }
                        for hit in result["hits"]
                    ],
                }
            )
        count = len(details)
        if count == 0:
            raise KnowledgeError("评估集中没有启用的题目")
        results[strategy] = {
            "n": count,
            f"Hit@{k}": sum(row["hit"] for row in details) / count,
            f"Recall@{k}": sum(row["recall"] for row in details) / count,
            f"AllHit@{k}": sum(row["all_hit"] for row in details) / count,
            f"MRR@{k}": sum(row["reciprocal_rank"] for row in details) / count,
            f"Precision@{k}": sum(row["precision"] for row in details) / count,
            f"nDCG@{k}": sum(row["ndcg"] for row in details) / count,
            "latency_median_ms": statistics.median(latencies),
            "latency_p95_ms": sorted(latencies)[max(0, math.ceil(0.95 * count) - 1)],
            "seconds_per_query": (time.perf_counter() - started) / count,
            "details": details,
        }
    encoded = json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if retrieval.repository.revision != revision:
        raise ConflictError("评估期间知识库发生变化，请重新评估")
    return {
        "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
        "corpus": corpus,
        "latency_scope": "按策略顺序执行，共用有界查询向量缓存；冷启动和网络抖动影响耗时，不作为独立速度优劣结论",
        "source_map": aliases,
        "source_map_sha256": hashlib.sha256(
            json.dumps(aliases, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "source_coverage": {
            "required": len(required_sources),
            "available": len(required_sources),
            "missing": [],
        },
        "embedding_fingerprint": retrieval.embedder.fingerprint,
        "reranker_fingerprint": retrieval.reranker.fingerprint
        if "hybrid_rerank" in methods
        else "",
        "dataset_split": dataset.get("split", "未划分"),
        "human_review_status": dataset.get("human_review_status", "未复核"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_hash_scope": "JSON 解析后按键排序并以 UTF-8 序列化",
        "k": k,
        "candidate_chunks": min(50, k * 5),
        "configuration": retrieval.settings.public(),
        "index_revision": revision,
        "metrics_scope": "文档检索，不包含回答正确性或专家评价",
        "dataset_origin": dataset.get("generator_model", dataset.get("origin", "未标注")),
        "results": results,
    }


def write_report(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def derive_paper_metrics(report: dict, source_to_paper: dict[str, str]) -> dict:
    """从冻结的片段候选排名复算论文级指标，不重新检索或改写文档级结果。"""
    if not isinstance(report, dict):
        raise KnowledgeError("检索报告必须为 JSON 对象")
    k = report.get("k")
    if (
        type(k) is not int
        or not 1 <= k <= 50
        or not isinstance(report.get("results"), dict)
        or not report["results"]
    ):
        raise KnowledgeError("需要包含 k 和逐题候选证据的检索报告")
    if not source_to_paper or any(
        not isinstance(source, str) or not isinstance(paper, str) or not source or not paper
        for source, paper in source_to_paper.items()
    ):
        raise KnowledgeError("论文映射不能为空，且需使用非空字符串编号")
    names = ("Hit", "Recall", "AllHit", "MRR", "Precision", "nDCG")
    metrics = [f"{name}@{k}" for name in names]
    results = {}
    for strategy, original in report["results"].items():
        details = []
        for row in original["details"]:
            gold_sources = row.get("gold")
            candidates = row.get("evidence")
            if not isinstance(gold_sources, list) or not isinstance(candidates, list):
                raise KnowledgeError("逐题记录缺少标准出处或完整候选片段排名")
            unknown_gold = sorted(set(gold_sources) - source_to_paper.keys())
            if unknown_gold:
                raise KnowledgeError(f"标准出处缺少论文映射：{unknown_gold[0]}")
            gold = {source_to_paper[source] for source in gold_sources}
            if not gold:
                raise KnowledgeError("逐题记录缺少可计分的标准论文")
            ranked = []
            skipped = 0
            for candidate in candidates:
                source = candidate.get("source_id")
                paper = source_to_paper.get(source)
                if paper is None:
                    skipped += 1
                elif paper not in ranked:
                    ranked.append(paper)
            top = ranked[:k]
            positions = [position + 1 for position, paper in enumerate(top) if paper in gold]
            matched = len(gold.intersection(top))
            details.append(
                {
                    "qid": row["qid"],
                    "gold_papers": sorted(gold),
                    "ranked_papers": top,
                    "hit": bool(matched),
                    "recall": matched / len(gold),
                    "all_hit": matched == len(gold),
                    "reciprocal_rank": 1 / min(positions) if positions else 0.0,
                    "precision": matched / k,
                    "ndcg": sum(1 / math.log2(position + 1) for position in positions)
                    / sum(
                        1 / math.log2(position + 1) for position in range(1, min(len(gold), k) + 1)
                    ),
                    "skipped_non_paper_candidates": skipped,
                    "document_hit": row["hit"],
                }
            )
        count = len(details)
        if not count:
            raise KnowledgeError("检索报告没有可复算的题目")
        results[strategy] = {
            "n": count,
            metrics[0]: sum(row["hit"] for row in details) / count,
            metrics[1]: sum(row["recall"] for row in details) / count,
            metrics[2]: sum(row["all_hit"] for row in details) / count,
            metrics[3]: sum(row["reciprocal_rank"] for row in details) / count,
            metrics[4]: sum(row["precision"] for row in details) / count,
            metrics[5]: sum(row["ndcg"] for row in details) / count,
            "document_level_reference": {name: original[name] for name in metrics},
            "details": details,
        }
    return {
        "metrics_scope": "冻结候选片段按正式编号归并后的论文级检索；不是重新检索或答案正确性评估",
        "dataset_sha256": report.get("dataset_sha256"),
        "corpus": report.get("corpus"),
        "index_revision": report.get("index_revision"),
        "embedding_fingerprint": report.get("embedding_fingerprint"),
        "reranker_fingerprint": report.get("reranker_fingerprint"),
        "candidate_chunks": report.get("candidate_chunks"),
        "dataset_split": report.get("dataset_split"),
        "dataset_origin": report.get("dataset_origin"),
        "human_review_status": report.get("human_review_status"),
        "k": k,
        "source_to_paper_sha256": hashlib.sha256(
            json.dumps(source_to_paper, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "results": results,
    }
