"""在 v26 隔离副本运行既有题检索与问答机器诊断。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.evaluation import derive_paper_metrics, evaluate, write_report  # noqa: E402
from biokb.providers import EmbeddingProvider, LanguageModel  # noqa: E402
from biokb.reranking import Reranker  # noqa: E402
from scripts.audit_v26_research import sha256_file  # noqa: E402

METHODS = ["bm25", "dense", "hybrid", "hybrid_rerank"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_database(source: Path, target: Path, expected_hash: str) -> dict:
    source = source.resolve(strict=True)
    if sha256_file(source) != expected_hash:
        raise ValueError("正式 v26 数据库摘要与审计冻结值不一致")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"实验副本已存在，停止以免覆盖历史结果：{target}")
    temporary = target.with_suffix(".tmp")
    if temporary.exists():
        raise FileExistsError(f"存在未完成的副本：{temporary}")
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy, pages=1000)
        with closing(sqlite3.connect(temporary)) as copy:
            integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {
                table: copy.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("documents", "chunks", "embeddings")
            }
        if integrity != "ok":
            raise ValueError("实验副本完整性检查失败")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    after_hash = sha256_file(source)
    if after_hash != expected_hash:
        raise ValueError("复制期间正式 v26 数据库发生变化")
    return {
        "source_database": str(source), "source_sha256_before_after": expected_hash,
        "source_unchanged": True, "snapshot_database": str(target),
        "snapshot_sha256_before_experiment": sha256_file(target),
        "integrity_check": integrity, "counts": counts,
    }


def source_map(services, local: dict) -> dict[str, str]:
    by_document = {}
    for paper in local["papers"]:
        for document_id in paper["document_ids"]:
            by_document[document_id] = paper["paper_id"]
    mapping = {}
    for document in services.repository.document_catalog():
        if document["id"] in by_document:
            source = str(document["metadata"].get("source_id") or document["id"])
            mapping[source] = by_document[document["id"]]
    return mapping


def prepare_fragment_cases(services, questions: dict) -> dict:
    cases = []
    for item in questions["items"]:
        source_id = item["gold_doc_ids"][0]
        quote = item["evidence_quote"].strip()
        with services.repository.connection() as connection:
            row = connection.execute(
                "SELECT id,text FROM documents WHERE json_extract(metadata,'$.source_id')=?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"开发题目标全文不在 v26 库中：{item['qid']}")
        start = row["text"].find(quote)
        cases.append({
            "qid": item["qid"], "question": item["question"],
            "source_id": source_id, "document_id": row["id"],
            "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "quote_chars": len(quote), "start_offset": start,
            "end_offset": start + len(quote) if start >= 0 else None,
            "exact_quote_found": start >= 0,
            "human_academic_review": None,
        })
    return {
        "origin": "既有 Codex 全文开发题及其摘录；非独立专家标准",
        "source_dataset_sha256": hashlib.sha256(
            json.dumps(questions, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "cases": cases,
    }


def interval_coverage(start: int, end: int, hits: list[dict], document_id: str) -> float:
    intervals = []
    for hit in hits:
        if hit["document_id"] == document_id:
            lower = max(start, hit["start_offset"])
            upper = min(end, hit["end_offset"])
            if lower < upper:
                intervals.append((lower, upper))
    intervals.sort()
    covered, right = 0, start
    for lower, upper in intervals:
        covered += max(0, upper - max(lower, right))
        right = max(right, upper)
    return covered / max(1, end - start)


def run_fragments(services, cases: dict, method: str) -> dict:
    details = []
    for item in cases["cases"]:
        if not item["exact_quote_found"]:
            details.append({"qid": item["qid"], "scored": False, "reason": "摘录未在 v26 原文精确定位"})
            continue
        result = services.retrieval.search(item["question"], method, top_k=50)
        hits = result["hits"]
        detail = {"qid": item["qid"], "scored": True, "source_id": item["source_id"],
                  "quote_sha256": item["quote_sha256"], "quote_chars": item["quote_chars"]}
        for k in (5, 10):
            selected = hits[:k]
            coverage = interval_coverage(item["start_offset"], item["end_offset"],
                                         selected, item["document_id"])
            detail[f"coverage@{k}"] = coverage
            detail[f"any_overlap@{k}"] = coverage > 0
            detail[f"coverage_95@{k}"] = coverage >= 0.95
        detail["candidate_chunks"] = [
            {"rank": rank, "chunk_id": hit["chunk_id"],
             "source_id": hit["metadata"].get("source_id", hit["document_id"]),
             "start_offset": hit["start_offset"], "end_offset": hit["end_offset"],
             "score": hit["score"], "rerank_score": hit.get("rerank_score")}
            for rank, hit in enumerate(hits, 1)
        ]
        details.append(detail)
    scored = [row for row in details if row["scored"]]
    return {
        "method": method, "n_scored": len(scored), "n_unresolved": len(details) - len(scored),
        "scope": "既有开发题摘录的原文字符区间覆盖；重叠不等于证据足以支持答案",
        "any_overlap@5": sum(row["any_overlap@5"] for row in scored) / len(scored) if scored else None,
        "any_overlap@10": sum(row["any_overlap@10"] for row in scored) / len(scored) if scored else None,
        "coverage_95@5": sum(row["coverage_95@5"] for row in scored) / len(scored) if scored else None,
        "coverage_95@10": sum(row["coverage_95@10"] for row in scored) / len(scored) if scored else None,
        "mean_coverage@5": statistics.mean(row["coverage@5"] for row in scored) if scored else None,
        "mean_coverage@10": statistics.mean(row["coverage@10"] for row in scored) if scored else None,
        "details": details,
    }


def run_qa(services, cases: dict, output: Path) -> dict:
    if services.settings.llm_backend == "disabled":
        return {"status": "skipped", "reason": "未配置生成模型"}
    results = []
    for item in cases["cases"]:
        print(f"问答机器诊断 {item['qid']}", flush=True)
        try:
            answer = services.rag.ask(item["question"], "hybrid_rerank", top_k=5)
            citations = answer["citations"]
            valid = all(
                citation["text"] == services.repository.document(
                    citation["document_id"], include_chunks=False
                )["text"][
                    citation["start_offset"]:citation["end_offset"]
                ] for citation in citations
            )
            coverage = interval_coverage(item["start_offset"], item["end_offset"],
                                         [row for row in citations if row["used"]],
                                         item["document_id"]) if item["exact_quote_found"] else None
            results.append({
                "qid": item["qid"], "mode": answer["mode"],
                "answer": answer["answer"], "citation_count": len(citations),
                "used_citation_count": sum(row["used"] for row in citations),
                "citation_offsets_exact": valid,
                "target_quote_coverage_by_used_citations": coverage,
                "generation_validation": answer["generation_validation"],
                "generation_fingerprint": answer["generation_fingerprint"],
                "retrieval_revision": answer["retrieval"]["index_revision"],
                "warnings": answer["warnings"],
                "human_correctness": None, "human_evidence_support": None,
                "human_completeness": None,
            })
        except Exception as exc:
            results.append({"qid": item["qid"], "error_type": type(exc).__name__,
                            "human_correctness": None, "human_evidence_support": None})
        write_report({"status": "running", "results": results}, output / "qa_machine_diagnostic.json")
    report = {
        "status": "complete", "scope": "仅机器核查格式、引用位置和开发摘录覆盖；无学术正确率",
        "n": len(results), "generated_or_partial": sum(row.get("mode") in {"generated", "partial"} for row in results),
        "citation_offsets_exact": sum(row.get("citation_offsets_exact", False) for row in results),
        "results": results,
    }
    write_report(report, output / "qa_machine_diagnostic.json")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--local-audit", type=Path, required=True)
    parser.add_argument("--legacy-dataset", type=Path, required=True)
    parser.add_argument("--fulltext-questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-qa", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("实验目录已存在；使用新路径以保留既有结果")
    output.mkdir(parents=True)
    local = json.loads(args.local_audit.read_text(encoding="utf-8"))
    dataset = json.loads(args.legacy_dataset.read_text(encoding="utf-8"))
    questions = json.loads(args.fulltext_questions.read_text(encoding="utf-8"))
    settings = Settings.from_env()
    embedder = EmbeddingProvider(settings)
    reranker = Reranker(settings)
    if settings.embedding_backend == "hash" or settings.reranker_backend == "disabled":
        raise ValueError("本实验需要真实向量模型和重排序模型")
    profiles = local["vector_profiles"]
    if len(profiles) != 1 or profiles[0]["fingerprint"] != embedder.fingerprint:
        raise ValueError("配置的向量模型指纹与 v26 已存索引不一致")
    snapshot = snapshot_database(args.database, output / "snapshot" / "knowledge.sqlite3",
                                 local["database_sha256_before"])
    write_report(snapshot, output / "snapshot_manifest.json")
    runtime_settings = replace(settings, data_dir=output / "snapshot",
                               request_timeout=max(90, settings.request_timeout), workers=1)
    services = build_services(runtime_settings)
    try:
        frozen = {
            "frozen_at_utc": utc_now(), "purpose": "v26 机器诊断，非科研正确率验收",
            "corpus": services.repository.corpus_manifest(),
            "index_revision": services.repository.revision,
            "database_sha256": local["database_sha256_before"],
            "legacy_dataset_file_sha256": sha256_file(args.legacy_dataset),
            "fulltext_questions_file_sha256": sha256_file(args.fulltext_questions),
            "legacy_dataset_origin": dataset.get("origin"),
            "legacy_dataset_size": len(dataset["items"]),
            "methods": METHODS, "k": 10, "candidate_chunks": 50,
            "embedding_backend": settings.embedding_backend,
            "embedding_model": settings.embedding_model,
            "embedding_fingerprint": embedder.fingerprint,
            "reranker_model": settings.reranker_model,
            "reranker_fingerprint": reranker.fingerprint,
            "llm_model": settings.llm_model, "llm_fingerprint": LanguageModel(settings).fingerprint,
            "model_key_values_recorded": False,
            "latency_limit": "策略按固定顺序运行，共享查询向量缓存和外部服务；耗时只作本次运行记录",
            "human_review_status": None,
        }
        write_report(frozen, output / "protocol.json")
        cases = prepare_fragment_cases(services, questions)
        write_report(cases, output / "fragment_cases.json")
        mapping = source_map(services, local)
        write_report(mapping, output / "source_to_paper.json")
        for method in METHODS:
            print(f"开始 69 道旧模型生成题：{method}", flush=True)
            report = evaluate(services.retrieval, dataset, [method], 10)
            write_report(report, output / f"legacy_{method}.json")
            papers = derive_paper_metrics(report, mapping)
            write_report(papers, output / f"legacy_paper_{method}.json")
            print(f"开始 6 道全文开发题：{method}", flush=True)
            fragment = run_fragments(services, cases, method)
            write_report(fragment, output / f"fragments_{method}.json")
            print(json.dumps({"method": method,
                              "legacy_Hit@10": report["results"][method]["Hit@10"],
                              "fragment_any_overlap@5": fragment["any_overlap@5"]},
                             ensure_ascii=False), flush=True)
        qa = {"status": "skipped_by_command"} if args.skip_qa else run_qa(services, cases, output)
        write_report({key: qa[key] for key in qa if key != "results"}, output / "qa_summary.json")
    finally:
        services.jobs.close()
    source_after = sha256_file(args.database)
    preservation = {
        "checked_at_utc": utc_now(), "formal_database_sha256_before": local["database_sha256_before"],
        "formal_database_sha256_after": source_after,
        "formal_database_unchanged": source_after == local["database_sha256_before"],
        "snapshot_sha256_after_experiment": sha256_file(snapshot_path)
        if (snapshot_path := output / "snapshot" / "knowledge.sqlite3").exists() else None,
    }
    write_report(preservation, output / "preservation_after.json")
    return 0 if preservation["formal_database_unchanged"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
