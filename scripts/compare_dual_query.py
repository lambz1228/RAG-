"""在独立副本上比较原查询与中英双路查询；已有留出题仅作诊断回归。"""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.experiments import evidence_coverage, file_sha256, write_json  # noqa: E402
from biokb.rag import RAGService  # noqa: E402


def run(database, dataset, output):
    before = file_sha256(database)
    questions = json.loads(dataset.read_text(encoding="utf-8"))["items"]
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for enabled, name in ((False, "original"), (True, "dual")):
        snapshot = output / name
        snapshot.mkdir()
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(snapshot / "knowledge.sqlite3")) as dst:
                src.backup(dst)
        settings = replace(Settings.from_env(), data_dir=snapshot, query_translation=enabled)
        services = build_services(settings)
        # 仅测上下文选择，不调用回答生成；转换接口仍按其真实配置运行。
        selector = RAGService(
            services.repository,
            services.retrieval,
            services.rag.model,
            replace(settings, llm_backend="disabled"),
        )
        try:
            for question in questions:
                query = question["question"]
                result = services.retrieval.search(query, "hybrid", 50)
                spans = question.get("evidence", [])
                ranks = [
                    i
                    for i, h in enumerate(result["hits"], 1)
                    if any(
                        h["document_id"] == g["document_id"]
                        and h["start_offset"] < g["end_offset"]
                        and g["start_offset"] < h["end_offset"]
                        for g in spans
                    )
                ]
                context = selector.ask(query, "hybrid", 5, context_neighbors=1)
                rank = min(ranks) if ranks else None
                record = {
                    "qid": question["qid"],
                    "arm": name,
                    "question": query,
                    "answerable": question["answerable"],
                    "first_gold_span_rank": rank,
                    "context_span_coverage": evidence_coverage(spans, context["citations"]),
                    "translation": result.get("query_translation"),
                    "warnings": result["warnings"],
                    "hits": result["hits"],
                    "context": context["citations"],
                }
                records.append(record)
                with (output / "diagnostics.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{name} {question['qid']} rank={rank}", flush=True)
        finally:
            services.jobs.close()
    summary = {
        "scope": "已观察题集上的检索诊断，不是新独立测试",
        "source_unchanged": file_sha256(database) == before,
        "dataset_sha256": file_sha256(dataset),
        "arms": {},
    }
    for name in ("original", "dual"):
        rows = [r for r in records if r["arm"] == name and r["answerable"]]
        summary["arms"][name] = {
            "n": len(rows),
            "gold_span_in_top50": sum(r["first_gold_span_rank"] is not None for r in rows),
            "gold_span_in_top5": sum(
                r["first_gold_span_rank"] is not None and r["first_gold_span_rank"] <= 5
                for r in rows
            ),
            "positive_context_coverage": sum(r["context_span_coverage"] > 0 for r in rows),
            "context_coverage_mean": sum(r["context_span_coverage"] for r in rows) / len(rows),
            "translation_fallbacks": sum(
                (r["translation"] or {}).get("status") == "fallback" for r in rows
            ),
        }
    write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.dataset, args.output), ensure_ascii=False, indent=2))
