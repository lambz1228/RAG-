"""固定配置的问答消融：保存完整证据和盲评材料，不自动裁定学术正确性。"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .container import build_services
from .errors import KnowledgeError, ProviderError
from .providers import PROMPT_VERSION, UNGROUNDED_PROMPT_VERSION
from .rag import RAGService
from .retrieval import validate_query


@dataclass(frozen=True)
class Arm:
    name: str
    strategy: str
    top_k: int = 5
    neighbors: int = 0
    document_cap: int = 2


ARMS = (
    Arm("no_rag", "none"),
    Arm("bm25", "bm25"),
    Arm("dense", "dense"),
    Arm("hybrid_rerank", "hybrid_rerank"),
    Arm("neighbors", "hybrid_rerank", neighbors=1),
    Arm("wide", "hybrid_rerank", top_k=10, document_cap=5),
    Arm("neighbors_wide", "hybrid_rerank", top_k=10, neighbors=1, document_cap=5),
)
OFFLINE_ARMS = (
    Arm("bm25", "bm25"),
    Arm("hybrid", "hybrid"),
    Arm("neighbors", "hybrid", neighbors=1),
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_questions(path: Path) -> tuple[list[dict], dict]:
    """兼容冻结题库与演示 JSON；只把 question 字段交给模型。"""
    if path.is_dir():
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        source = path / "questions.jsonl"
        if file_sha256(source) != manifest["questions_file_sha256"]:
            raise KnowledgeError("冻结题库的 SHA-256 不匹配")
        raw = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(raw) != manifest["question_count"]:
            raise KnowledgeError("冻结题库数量与清单不一致")
    else:
        source = path
        obj = json.loads(path.read_text(encoding="utf-8"))
        manifest, raw = obj, obj["items"]
    questions, seen = [], set()
    for row in raw:
        qid = row.get("qid", row.get("题号"))
        question = row.get("question", row.get("研究问题"))
        if not isinstance(qid, str) or not qid.strip() or qid in seen:
            raise KnowledgeError("题号必须非空且唯一")
        validate_query(question, 5)
        seen.add(qid)
        questions.append(
            {
                "qid": qid,
                "question": question,
                "type": row.get("type", row.get("题型", "未分类")),
                "reference": row,
            }
        )
    if not questions:
        raise KnowledgeError("题集不能为空")
    return questions, {"sha256": file_sha256(source), "manifest": manifest}


def evidence_coverage(gold: list[dict], selected: list[dict]) -> float | None:
    """原文区间的并集覆盖比例；重叠证据不重复计数，不称为原子证据召回。"""
    if not gold:
        return None
    ratios = []
    for span in gold:
        start, end = span["start_offset"], span["end_offset"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise KnowledgeError("标准证据区间不合法")
        intervals = []
        for item in selected:
            if span["document_id"] != item["document_id"]:
                continue
            left, right = max(start, item["start_offset"]), min(end, item["end_offset"])
            if right > left:
                intervals.append((left, right))
        covered, cursor = 0, start
        for left, right in sorted(intervals):
            covered += max(0, right - max(left, cursor))
            cursor = max(cursor, right)
        ratios.append(covered / (end - start))
    return sum(ratios) / len(ratios)


def gold_spans(reference: dict) -> list[dict]:
    if "evidence" in reference:
        return reference["evidence"]
    if reference.get("题型") == "无答案":
        return []
    try:
        return [
            {
                "document_id": reference["库内文档ID"],
                "start_offset": int(reference["标准证据起始字符"]),
                "end_offset": int(reference["标准证据结束字符"]),
            }
        ]
    except (KeyError, ValueError, TypeError):
        return []


def export_review(output: Path, questions: list[dict], results: list[dict], seed: int) -> None:
    """公开表只给匿名答卷号；对照组和参考答案独立保存供协调者使用。"""
    rows = list(results)
    random.Random(seed).shuffle(rows)
    by_id = {item["qid"]: item for item in questions}
    public, keys = [], []
    for number, result in enumerate(rows, 1):
        blind_id = f"B{number:05d}"
        item = by_id[result["qid"]]
        public.append(
            {
                "答卷号": blind_id,
                "问题": item["question"],
                "回答": result.get("answer", ""),
                "运行状态": "失败" if "error_type" in result else "完成",
                "引用原文": json.dumps(
                    [
                        {"number": c["number"], "text": c["text"], "title": c["title"]}
                        for c in result.get("citations", [])
                        if c.get("used")
                    ],
                    ensure_ascii=False,
                ),
                "事实正确性(0/1/2)": "",
                "证据支持(0/1/2)": "",
                "完整性(0/1/2)": "",
                "拒答恰当性(恰当/漏答/误答)": "",
                "无支持断言数": "",
                "评分者": "",
                "评分时间": "",
                "备注": "",
            }
        )
        keys.append(
            {
                "答卷号": blind_id,
                "qid": item["qid"],
                "arm": result["arm"],
                "reference": item["reference"],
            }
        )
    with (output / "盲评表.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(public[0]))
        writer.writeheader()
        writer.writerows(public)
    write_json(output / "协调者密钥_勿分发给盲评者.json", keys)


def run_experiment(
    database: Path,
    dataset: Path,
    output: Path,
    settings: Settings,
    offline: bool = False,
    seed: int = 20261115,
) -> dict:
    """正式源库只读备份；任何失败保留已完成记录，不覆盖已有运行。"""
    database = database.resolve(strict=True)
    questions, dataset_info = load_questions(dataset)
    expected = dataset_info["manifest"].get("database_sha256")
    before = file_sha256(database)
    if expected and expected != before:
        raise KnowledgeError("正式库与题库冻结时的数据库摘要不同")
    if not offline and (
        settings.embedding_backend == "hash"
        or settings.llm_backend == "disabled"
        or settings.reranker_backend == "disabled"
    ):
        raise KnowledgeError(
            "真实模型实验需要配置向量、生成、重排序模型；离线演示请显式使用 --offline"
        )
    if offline:
        settings = Settings(
            embedding_backend="hash",
            llm_backend="disabled",
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
    arms = OFFLINE_ARMS if offline else ARMS
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot" / "knowledge.sqlite3"
    snapshot.parent.mkdir()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(snapshot)) as copied:
            original.backup(copied)
    runtime = replace(settings, data_dir=snapshot.parent, workers=1)
    services = build_services(runtime)
    results = []
    error = None
    revision = services.repository.revision
    try:
        rows = services.repository.index_rows()
        if not rows or any(
            row["fingerprint"] != services.retrieval.embedder.fingerprint for row in rows
        ):
            raise KnowledgeError("实验索引为空或向量指纹不符；先在独立副本中完成索引重建")
        for item in questions:
            for span in gold_spans(item["reference"]):
                doc = services.repository.document(span["document_id"])
                start, end = span["start_offset"], span["end_offset"]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not 0 <= start < end <= len(doc["text"])
                ):
                    raise KnowledgeError("标准证据区间与实验库不匹配")
                expected_span = span.get("sha256") or item["reference"].get("原文文件SHA256")
                if (
                    expected_span
                    and hashlib.sha256(doc["text"][start:end].encode()).hexdigest() != expected_span
                ):
                    raise KnowledgeError("标准证据区间摘要不匹配")
        protocol = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "scope": "离线流程演示；无学术成绩"
            if offline
            else "固定配置问答对照；正确性待人工盲评",
            "dataset": dataset_info,
            "database_sha256": before,
            "snapshot_sha256": file_sha256(snapshot),
            "corpus": services.repository.corpus_manifest(),
            "index_revision": revision,
            "configuration": runtime.public(),
            "arms": [asdict(arm) for arm in arms],
            "generation_fingerprint": services.rag.model.fingerprint,
            "embedding_fingerprint": services.retrieval.embedder.fingerprint,
            "reranker_fingerprint": services.retrieval.reranker.fingerprint,
            "prompt_version": PROMPT_VERSION,
            "no_rag_prompt_version": UNGROUNDED_PROMPT_VERSION,
            "human_review_status": None,
            "latency_scope": "查询内随机对照组顺序；共享缓存与网络，不作受控速度结论",
        }
        write_json(output / "protocol.json", protocol)
        randomizer = random.Random(seed)
        for item in questions:
            order = list(arms)
            randomizer.shuffle(order)
            for arm in order:
                start = time.perf_counter()
                record = {"qid": item["qid"], "arm": arm.name}
                try:
                    if arm.strategy == "none":
                        response = services.rag.model.answer_without_retrieval(item["question"])
                        if (
                            response.get("citations") != []
                            or response.get("status") not in {"answered", "partial", "insufficient"}
                            or not isinstance(response.get("answer"), str)
                            or not response["answer"].strip()
                        ):
                            raise ProviderError("无检索模型输出格式错误")
                        record.update(
                            answer=response["answer"],
                            mode="ungrounded_" + response["status"],
                            citations=[],
                        )
                    else:
                        rag = RAGService(
                            services.repository,
                            services.retrieval,
                            services.rag.model,
                            replace(runtime, max_evidence_per_document=arm.document_cap),
                        )
                        answer = rag.ask(
                            item["question"],
                            arm.strategy,
                            top_k=arm.top_k,
                            context_neighbors=arm.neighbors,
                        )
                        record.update(
                            answer=answer["answer"],
                            mode=answer["mode"],
                            citations=answer["citations"],
                            context=answer["context"],
                            warnings=answer["warnings"],
                            generation_validation=answer["generation_validation"],
                        )
                    if services.repository.revision != revision:
                        raise KnowledgeError("实验期间索引变化")
                    gold = gold_spans(item["reference"])
                    record["context_span_coverage"] = (
                        evidence_coverage(gold, record["citations"])
                        if arm.strategy != "none"
                        else None
                    )
                    record["cited_span_coverage"] = (
                        evidence_coverage(gold, [c for c in record["citations"] if c.get("used")])
                        if arm.strategy != "none"
                        else None
                    )
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                record["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
                results.append(record)
                with (output / "results.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        export_review(output, questions, results, seed)
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        services.jobs.close()
        summary = {
            "question_count": len(questions),
            "planned_answers": len(questions) * len(arms),
            "completed_records": len(results),
            "errors": sum("error_type" in r for r in results),
            "fatal_error_type": error,
            "source_unchanged": file_sha256(database) == before,
            "academic_correctness_rate": None,
            "human_review_status": None,
            "scope": "离线流程验证" if offline else "待人工复核的系统输出",
        }
        write_json(output / "summary.json", summary)
    if not summary["source_unchanged"]:
        raise KnowledgeError("源数据库在实验期间发生变化，本轮结果不可用")
    return summary
