"""v1-machine 冻结题集盲审运行：隔离副本、登记指纹、产出盲评表与保密答案密钥。

口径：本运行产生系统回答供人工盲评；不产出任何学术正确率数字。
正式库只读，运行在与正式库内容指纹一致的 sqlite 备份副本上进行。
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.providers import EmbeddingProvider, LanguageModel  # noqa: E402
from biokb.reranking import Reranker  # noqa: E402
from scripts.machine_annotate import content_fingerprint  # noqa: E402
from scripts.questionbank import sha256_file  # noqa: E402

METHOD = "hybrid_rerank"
TOP_K = 5
BLIND_FIELDS = [
    "题号", "题型", "研究问题", "系统回答", "系统回答模式", "系统引用",
    "事实正确性(0/1/2)", "证据支持(0/1/2)", "完整性(0/1/2)",
    "拒答恰当性(恰当/漏答/误答)", "无支持断言数", "评分者", "评分时间",
]
KEY_FIELDS = [
    "题号", "题型", "原子答案要点", "无答案或冲突说明", "PMID", "PMCID", "DOI",
    "标准证据起始字符", "标准证据结束字符", "原文文件SHA256", "机器备注",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(payload: dict, path: Path) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_database(source: Path, target: Path) -> None:
    """sqlite 备份生成一次性运行副本；已存在则停止，保证一次运行不覆盖。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"运行副本已存在，停止以免混淆多次运行：{target}")
    temporary = target.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy, pages=1000)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questionbank", type=Path, default=Path("questionbank/v1-machine"))
    parser.add_argument("--database", type=Path,
                        default=Path("data/biokb-v26-authors-20261105/knowledge.sqlite3"))
    parser.add_argument("--local-audit", type=Path,
                        default=Path("output/research_quality_v26_20261105/local_audit.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("output/blind_review_v1machine_20261113"))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("运行目录已存在；每次运行使用新目录以保留历史结果")
    output.mkdir(parents=True)

    bank = args.questionbank.resolve(strict=True)
    manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
    questions_file = bank / "questions.jsonl"
    questions_sha = sha256_file(questions_file)
    if questions_sha != manifest["questions_file_sha256"]:
        raise ValueError("题集文件摘要与冻结清单不一致，停止运行")
    database = args.database.resolve(strict=True)
    database_sha = sha256_file(database)
    if database_sha != manifest["database_sha256"]:
        raise ValueError("正式库摘要与题集冻结时登记的摘要不一致，停止运行")
    questions = [json.loads(line) for line in
                 questions_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"题集 {manifest['version']}：{len(questions)} 题，SHA-256 核对通过", flush=True)

    settings = Settings.from_env()
    embedder, reranker = EmbeddingProvider(settings), Reranker(settings)
    if settings.embedding_backend == "hash" or settings.reranker_backend == "disabled":
        raise ValueError("盲审运行需要真实向量模型和重排序模型")
    audit = json.loads(args.local_audit.resolve(strict=True).read_text(encoding="utf-8"))
    profiles = audit["vector_profiles"]
    if len(profiles) != 1 or profiles[0]["fingerprint"] != embedder.fingerprint:
        raise ValueError("配置的向量模型指纹与 v26 已存索引不一致")

    snapshot = output / "snapshot" / "knowledge.sqlite3"
    snapshot_database(database, snapshot)
    if content_fingerprint(snapshot) != content_fingerprint(database):
        raise ValueError("运行副本与正式库内容指纹不一致")
    runtime = replace(settings, data_dir=snapshot.parent,
                      request_timeout=max(90, settings.request_timeout), workers=1)
    services = build_services(runtime)

    write_json({
        "purpose": "v1-machine 机器标注题集的盲审运行；评分由真实评审者完成",
        "frozen_at_utc": utc_now(),
        "questionbank_version": manifest["version"],
        "questionbank_frozen_at_utc": manifest["frozen_at_utc"],
        "questions_file_sha256": questions_sha,
        "database_sha256": database_sha,
        "corpus": services.repository.corpus_manifest(),
        "index_revision": services.repository.revision,
        "method": METHOD, "top_k": TOP_K,
        "embedding_model": settings.embedding_model,
        "embedding_fingerprint": embedder.fingerprint,
        "reranker_model": settings.reranker_model,
        "reranker_fingerprint": reranker.fingerprint,
        "llm_model": settings.llm_model,
        "llm_fingerprint": LanguageModel(settings).fingerprint,
        "model_key_values_recorded": False,
        "human_review_status": None,
    }, output / "protocol.json")

    results_path = output / "results.jsonl"
    results = []
    try:
        for index, item in enumerate(questions, 1):
            print(f"[{utc_now()}] 盲审运行 {index}/{len(questions)} {item['题号']}", flush=True)
            try:
                answer = services.rag.ask(item["研究问题"], METHOD, top_k=TOP_K)
                record = {
                    "题号": item["题号"], "题型": item["题型"], "mode": answer["mode"],
                    "answer": answer["answer"],
                    "citations": [
                        {"number": c["number"], "used": c["used"],
                         "document_id": c["document_id"],
                         "start_offset": c["start_offset"], "end_offset": c["end_offset"]}
                        for c in answer["citations"]
                    ],
                    "warnings": answer["warnings"],
                    "generation_validation": answer["generation_validation"],
                    "generation_fingerprint": answer["generation_fingerprint"],
                    "index_revision": answer["retrieval"]["index_revision"],
                }
            except Exception as exc:  # 单题失败不中断
                record = {"题号": item["题号"], "题型": item["题型"],
                          "error_type": type(exc).__name__, "error": str(exc)}
            results.append(record)
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    finally:
        services.jobs.close()

    with (output / "盲评表_无参考答案.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BLIND_FIELDS)
        writer.writeheader()
        by_qid = {row["题号"]: row for row in results}
        for item in questions:
            record = by_qid.get(item["题号"], {})
            citations = "；".join(
                f"[{c['number']}{'√' if c['used'] else '×'}]{c['document_id']}:{c['start_offset']}-{c['end_offset']}"
                for c in record.get("citations", []))
            writer.writerow({
                "题号": item["题号"], "题型": item["题型"], "研究问题": item["研究问题"],
                "系统回答": record.get("answer", f"运行失败：{record.get('error_type', '')}"),
                "系统回答模式": record.get("mode", ""), "系统引用": citations,
            })
    with (output / "评分用答案密钥_盲评前保密.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=KEY_FIELDS)
        writer.writeheader()
        for item in questions:
            writer.writerow({
                "题号": item["题号"], "题型": item["题型"],
                "原子答案要点": item["原子答案要点"],
                "无答案或冲突说明": item["无答案或冲突说明"],
                "PMID": item["PMID"], "PMCID": item["PMCID"], "DOI": item["DOI"],
                "标准证据起始字符": item["标准证据起始字符"],
                "标准证据结束字符": item["标准证据结束字符"],
                "原文文件SHA256": item["原文文件SHA256"], "机器备注": item["备注"],
            })
    summary = {
        "completed_at_utc": utc_now(),
        "question_count": len(questions),
        "answered": sum(1 for row in results if row.get("mode") in {"generated", "partial"}),
        "insufficient_evidence": sum(1 for row in results if row.get("mode") == "insufficient_evidence"),
        "errors": sum(1 for row in results if "error_type" in row),
        "scope": "盲审运行记录；无学术正确率，评分待真实评审者填写",
        "formal_database_sha256_after": sha256_file(database),
        "formal_database_unchanged": sha256_file(database) == database_sha,
    }
    write_json(summary, output / "run_summary.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["formal_database_unchanged"] and not summary["errors"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
