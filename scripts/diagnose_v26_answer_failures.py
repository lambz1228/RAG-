"""定位 v26 两道开发题的端到端缺陷环节，并可选在隔离副本做真实模型回归。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.providers import EmbeddingProvider  # noqa: E402

QIDS = ("fulltext-03", "fulltext-04")
STRATEGIES = ("bm25", "dense", "hybrid", "hybrid_rerank")
CONFIG_SETS = {
    "default": (
        ("baseline", 5, 0, None),
        ("neighbors_1", 5, 1, None),
        ("top_k_10", 10, 0, None),
        ("top_k_10_neighbors_1", 10, 1, None),
    ),
    "cap": (
        ("top_k_10_cap_4", 10, 0, 4),
        ("top_k_10_cap_4_neighbors_1", 10, 1, 4),
    ),
    "cap5": (
        ("top_k_10_cap_5", 10, 0, 5),
        ("top_k_10_cap_5_neighbors_1", 10, 1, 5),
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def interval_coverage(start: int, end: int, rows: list[dict]) -> float:
    intervals = []
    for row in rows:
        lower, upper = max(start, row["start_offset"]), min(end, row["end_offset"])
        if lower < upper:
            intervals.append((lower, upper))
    intervals.sort()
    covered, right = 0, start
    for lower, upper in intervals:
        covered += max(0, upper - max(lower, right))
        right = max(right, upper)
    return covered / max(1, end - start)


def overlap(start: int, end: int, chunk: dict) -> int:
    return max(0, min(end, chunk["end_offset"]) - max(start, chunk["start_offset"]))


def answer_chunks(connection, case: dict) -> list[dict]:
    """返回与参考摘录相交的分块，按覆盖字符数降序。"""
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT id,ordinal,start_offset,end_offset,text FROM chunks "
            "WHERE document_id=? ORDER BY ordinal",
            (case["document_id"],),
        )
    ]
    spans = [
        {**row, "overlap_chars": overlap(case["start_offset"], case["end_offset"], row)}
        for row in rows
        if overlap(case["start_offset"], case["end_offset"], row)
    ]
    spans.sort(key=lambda row: -row["overlap_chars"])
    return spans


def reconstruct_selection(hits: list[dict], count: int, per_document_limit: int,
                          max_chars: int) -> tuple[list[dict], dict[str, str], int]:
    """复现 RAGService.ask 的证据筛选顺序，返回选中片段、跳过原因和剩余字符预算。"""
    selected: list[dict] = []
    reasons: dict[str, str] = {}
    documents: dict[str, int] = {}
    remaining = max_chars
    for hit in hits:
        if len(selected) >= count:
            reasons.setdefault(hit["chunk_id"], "count")
        elif documents.get(hit["document_id"], 0) >= per_document_limit:
            reasons.setdefault(hit["chunk_id"], "document_cap")
        elif hit["end_offset"] - hit["start_offset"] > remaining:
            reasons.setdefault(hit["chunk_id"], "budget")
        else:
            selected.append(hit)
            documents[hit["document_id"]] = documents.get(hit["document_id"], 0) + 1
            remaining -= hit["end_offset"] - hit["start_offset"]
    return selected, reasons, remaining


def classify(rank: int | None, selected_ids: list[str], chunk_id: str,
             reasons: dict[str, str]) -> tuple[str, str]:
    if chunk_id in selected_ids:
        return "generation", "目标片段已进入上下文，缺陷发生在生成或引用使用环节"
    if rank is None:
        return "retrieval", "目标片段未进入 50 个候选，缺陷发生在召回或融合排序环节"
    if reasons.get(chunk_id) == "document_cap":
        return "selection", "目标片段在候选中，但被同论文证据上限挤出上下文"
    if reasons.get(chunk_id) == "budget":
        return "selection", "目标片段在候选中，但被上下文字符预算挤出"
    return "selection", f"目标片段在候选中排名第 {rank}，超过 top_k，未进入上下文"


def analyse(experiment: Path, settings: Settings) -> dict:
    cases = {row["qid"]: row for row in read_json(experiment / "fragment_cases.json")["cases"]}
    snapshots = {row["question"]: row for row in read_json(experiment / "qa_frozen_snapshots.json")}
    fragments = {name: read_json(experiment / f"fragments_{name}.json") for name in STRATEGIES}
    diagnostics = {row["qid"]: row for row in read_json(
        experiment / "qa_machine_diagnostic.json")["results"]}
    database = experiment / "snapshot" / "knowledge.sqlite3"
    results = []
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for qid in QIDS:
            case = cases[qid]
            start, end = case["start_offset"], case["end_offset"]
            spans = answer_chunks(connection, case)
            answer_chunk = spans[0]
            candidates = next(
                row for row in fragments["hybrid_rerank"]["details"] if row["qid"] == qid
            )["candidate_chunks"]
            chunk_rows = {
                row["id"]: dict(row)
                for row in connection.execute(
                    "SELECT id,document_id,start_offset,end_offset FROM chunks "
                    f"WHERE id IN ({','.join('?' * len(candidates))})",
                    [row["chunk_id"] for row in candidates],
                )
            }
            hits = [
                {
                    "chunk_id": row["chunk_id"],
                    "document_id": chunk_rows[row["chunk_id"]]["document_id"],
                    "start_offset": row["start_offset"],
                    "end_offset": row["end_offset"],
                }
                for row in candidates
            ]
            selected, reasons, remaining = reconstruct_selection(
                hits, settings.top_k, settings.max_evidence_per_document,
                settings.max_context_chars,
            )
            selected_ids = [row["chunk_id"] for row in selected]
            snapshot = snapshots[case["question"]]
            frozen_ids = [row["chunk_id"] for row in snapshot["citations"]]
            rank = next(
                (row["rank"] for row in candidates if row["chunk_id"] == answer_chunk["id"]),
                None,
            )
            stage, explanation = classify(rank, selected_ids, answer_chunk["id"], reasons)
            per_strategy = {}
            for name in STRATEGIES:
                detail = next(row for row in fragments[name]["details"] if row["qid"] == qid)
                ranks = {row["chunk_id"]: row["rank"] for row in detail["candidate_chunks"]}
                per_strategy[name] = {
                    "coverage@5": round(detail["coverage@5"], 4),
                    "coverage@10": round(detail["coverage@10"], 4),
                    "target_chunk_ranks": {row["id"]: ranks.get(row["id"]) for row in spans},
                }
            results.append({
                "qid": qid,
                "question": case["question"],
                "source_id": case["source_id"],
                "document_id": case["document_id"],
                "reference_quote": {
                    "start_offset": start, "end_offset": end, "chars": end - start,
                    "sha256": case["quote_sha256"],
                    "spans_multiple_chunks": len(spans) > 1,
                },
                "overlapping_chunks": [
                    {key: row[key] for key in (
                        "id", "ordinal", "start_offset", "end_offset",
                        "overlap_chars",
                    )} | {"head": row["text"][:80] + "…"}
                    for row in spans
                ],
                "answer_bearing_chunk": {
                    "id": answer_chunk["id"],
                    "rerank_rank": rank,
                    "coverage_of_quote": round(
                        answer_chunk["overlap_chars"] / (end - start), 4),
                },
                "strategy_metrics": per_strategy,
                "reconstructed_selection": {
                    "selected_chunk_ids": selected_ids,
                    "skip_reasons": reasons,
                    "remaining_context_chars": remaining,
                    "matches_frozen_citations": selected_ids == frozen_ids,
                    "frozen_citation_chunk_ids": frozen_ids,
                },
                "frozen_qa": {
                    "mode": diagnostics[qid]["mode"],
                    "target_quote_coverage_by_used_citations": diagnostics[qid][
                        "target_quote_coverage_by_used_citations"],
                },
                "stage": stage,
                "explanation": explanation,
                "human_review_status": None,
            })
    return {
        "generated_at_utc": utc_now(),
        "scope": "仅定位环节，不构成学术正确率；候选、排序与引用均来自 v26 冻结产物",
        "experiment": str(experiment),
        "settings": {
            "top_k": settings.top_k,
            "max_evidence_per_document": settings.max_evidence_per_document,
            "max_context_chars": settings.max_context_chars,
            "retrieval_candidates": settings.retrieval_candidates,
        },
        "results": results,
    }


def replay(experiment: Path, output: Path, name: str, configs: tuple,
           settings: Settings) -> dict:
    source = experiment / "snapshot" / "knowledge.sqlite3"
    target_dir = output / name / "snapshot"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "knowledge.sqlite3"
    if target.exists():
        raise FileExistsError(f"回归副本已存在，停止以免覆盖：{target}")
    source_hash = sha256_file(source)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target)) as copy:
            original.backup(copy, pages=1000)
    if sha256_file(source) != source_hash:
        raise ValueError("复制期间隔离快照发生变化")
    runtime = replace(settings, data_dir=target_dir, workers=1)
    embedder = EmbeddingProvider(runtime)
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        stored = connection.execute("SELECT DISTINCT fingerprint FROM embeddings").fetchone()[0]
        cases = {row["qid"]: row for row in read_json(
            experiment / "fragment_cases.json")["cases"]}
        answer_ids = {
            qid: answer_chunks(connection, case)[0]["id"] for qid, case in cases.items()
        }
    if stored != embedder.fingerprint:
        raise ValueError("当前向量模型指纹与 v26 索引不一致，拒绝回归")
    rows = []
    for config_name, count, neighbors, per_document_limit in configs:
        run_settings = (
            runtime if per_document_limit is None
            else replace(runtime, max_evidence_per_document=per_document_limit)
        )
        services = build_services(run_settings)
        try:
            for qid in QIDS:
                case = cases[qid]
                print(f"回归 {qid} {config_name}", flush=True)
                answer = services.rag.ask(
                    case["question"], "hybrid_rerank", top_k=count,
                    context_neighbors=neighbors,
                )
                used = [citation for citation in answer["citations"] if citation["used"]]
                covered_chunks = [
                    chunk_id
                    for citation in answer["citations"]
                    for chunk_id in (
                        citation.get("context_chunk_ids") or [citation["chunk_id"]]
                    )
                ]
                rows.append({
                    "qid": qid,
                    "configuration": config_name,
                    "top_k": count,
                    "context_neighbors": neighbors,
                    "max_evidence_per_document": run_settings.max_evidence_per_document,
                    "mode": answer["mode"],
                    "answer": answer["answer"],
                    "used_chunks": [citation["chunk_id"] for citation in used],
                    "context_chunk_ids": [
                        citation["chunk_id"] for citation in answer["citations"]
                    ],
                    "answer_bearing_chunk_in_context": answer_ids[qid] in covered_chunks,
                    "target_quote_coverage_by_used_citations": interval_coverage(
                        case["start_offset"], case["end_offset"], used),
                    "context": answer["context"],
                    "generation_validation": answer["generation_validation"],
                    "human_correctness": None,
                    "human_evidence_support": None,
                    "human_completeness": None,
                })
                write_json(output / name / "results.json", {
                    "status": "running", "source_snapshot_sha256": source_hash,
                    "source_unchanged": sha256_file(source) == source_hash,
                    "rows": rows, "human_review_status": None,
                })
        finally:
            services.jobs.close()
    report = {
        "generated_at_utc": utc_now(),
        "scope": "定向回归，不计入独立集成绩；两项均非学术正确率",
        "source_snapshot_sha256": source_hash,
        "source_unchanged": sha256_file(source) == source_hash,
        "embedding_fingerprint": embedder.fingerprint,
        "models": {
            "embedding": runtime.embedding_model,
            "reranker": runtime.reranker_model,
            "llm": runtime.llm_model,
        },
        "rows": rows,
        "field_notes": {
            "answer_bearing_chunk_in_context":
                "仅统计直接引用该 chunk_id 的证据；邻居扩展窗口覆盖目标片段时该值可能为 false，"
                "应以 target_quote_coverage_by_used_citations 为准",
        },
        "human_review_status": None,
    }
    write_json(output / name / "results.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--config-set", choices=sorted(CONFIG_SETS), default="default")
    parser.add_argument("--regression-name", default="regression")
    args = parser.parse_args()
    experiment = args.experiment.resolve(strict=True)
    output = args.output.resolve()
    settings = Settings.from_env()
    root_cause = output / "failure_root_cause.json"
    if root_cause.exists():
        report = read_json(root_cause)
        print("复用既有根因报告", flush=True)
    else:
        report = analyse(experiment, settings)
        write_json(root_cause, report)
    print(json.dumps([
        {"qid": row["qid"], "stage": row["stage"],
         "answer_bearing_chunk": row["answer_bearing_chunk"],
         "selection_matches_frozen": row["reconstructed_selection"]["matches_frozen_citations"]}
        for row in report["results"]
    ], ensure_ascii=False, indent=2))
    if args.replay:
        if settings.llm_backend == "disabled" or settings.embedding_backend == "hash":
            parser.error("回归需要真实向量、重排序与生成模型")
        regression = replay(experiment, output, args.regression_name,
                            CONFIG_SETS[args.config_set], settings)
        print(json.dumps([
            {"qid": row["qid"], "config": row["configuration"], "mode": row["mode"],
             "in_context": row["answer_bearing_chunk_in_context"],
             "coverage": round(row["target_quote_coverage_by_used_citations"], 3)}
            for row in regression["rows"]
        ], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
