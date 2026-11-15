"""只读核对科研文献、冻结问答证据和原文定位，生成待人工复核材料。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.papers import build_catalog, identifiers  # noqa: E402


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_database(path: Path) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        documents = {
            row[0]: {
                "id": row[0],
                "title": row[1],
                "filename": row[2],
                "text": row[3],
                "metadata": json.loads(row[4]),
            }
            for row in connection.execute("SELECT id,title,filename,text,metadata FROM documents")
        }
        chunks = {
            row[0]: {
                "document_id": row[1],
                "text": row[2],
                "start": row[3],
                "end": row[4],
            }
            for row in connection.execute(
                "SELECT id,document_id,text,start_offset,end_offset FROM chunks"
            )
        }
        vector_counts = dict(
            connection.execute("SELECT fingerprint,COUNT(*) FROM embeddings GROUP BY fingerprint")
        )
        revision = connection.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[
            0
        ]
        connection.rollback()
    return {
        "documents": documents,
        "chunks": chunks,
        "vector_counts": vector_counts,
        "revision": revision,
    }


def overlap_length(start: int, end: int, intervals: list[tuple[int, int]]) -> int:
    clipped = sorted((max(start, a), min(end, b)) for a, b in intervals if a < end and b > start)
    total = 0
    cursor = start
    for left, right in clipped:
        total += max(0, right - max(cursor, left))
        cursor = max(cursor, right)
    return total


def check_citation(citation: dict, answer: str, database: dict) -> dict:
    document = database["documents"].get(citation.get("document_id"))
    chunk = database["chunks"].get(citation.get("chunk_id"))
    start = citation.get("start_offset")
    end = citation.get("end_offset")
    valid_span = (
        document is not None
        and type(start) is int
        and type(end) is int
        and 0 <= start < end <= len(document["text"])
    )
    number = citation.get("number")
    referenced = type(number) is int and bool(re.search(rf"\[{number}\]", answer))
    checks = {
        "document_exists": document is not None,
        "chunk_exists": chunk is not None,
        "chunk_belongs_to_document": chunk is not None
        and chunk["document_id"] == citation.get("document_id"),
        "source_id_matches": document is not None
        and document["metadata"].get("source_id") == citation.get("metadata", {}).get("source_id"),
        "chunk_matches_database_text": document is not None
        and chunk is not None
        and chunk["text"] == document["text"][chunk["start"] : chunk["end"]],
        "citation_span_matches_original": valid_span
        and citation.get("text") == document["text"][start:end],
        "chunk_inside_citation_span": valid_span
        and chunk is not None
        and start <= chunk["start"] < chunk["end"] <= end,
        "used_flag_matches_answer": citation.get("used") is referenced,
    }
    return {
        "number": number,
        "used": citation.get("used"),
        "source_id": citation.get("metadata", {}).get("source_id"),
        "document_id": citation.get("document_id"),
        "chunk_id": citation.get("chunk_id"),
        "start_offset": start,
        "end_offset": end,
        "text": citation.get("text"),
        "checks": checks,
        "passed": all(checks.values()),
    }


def source_paths() -> list[Path]:
    review_dir = ROOT / "output/upgrade_20261104/answer_review_release"
    fulltext_dir = ROOT / "data/research_corpus/upgrade_20261104/fulltext"
    paths = [
        ROOT / "data/biokb/knowledge.sqlite3",
        ROOT / "data/biokb-v21/knowledge.sqlite3",
        ROOT / "data/biokb-v21-semantic/knowledge.sqlite3",
        ROOT / "data/biokb-v21-semantic/knowledge.sqlite3-wal",
        ROOT / "output/upgrade_20261104/experiments/evaluation_semantic.json",
        ROOT / "output/research_upgrade_20261105/paper_metrics_evaluation.json",
        ROOT / "output/research_upgrade_20261105/replay_fulltext03.json",
        ROOT / "output/research_upgrade_20261105/replay_fulltext04_v2.json",
        ROOT / "scripts/audit_research_quality.py",
        review_dir / "answers.json",
        review_dir / "fulltext_questions.json",
        review_dir / "fulltext_retrieval.json",
        review_dir / "answer_protocol.json",
        fulltext_dir / "acquisition_report.json",
    ]
    for pmcid in ("PMC12142011", "PMC13199795", "PMC13065327"):
        paths.extend((fulltext_dir / f"{pmcid}.json", fulltext_dir / f"{pmcid}.xml"))
    return paths


def source_hashes(paths: list[Path]) -> dict[str, str | None]:
    return {str(path.relative_to(ROOT)).replace("\\", "/"): file_sha256(path) for path in paths}


def official_record(pmid: str, expected: dict) -> dict:
    query = urllib.parse.urlencode(
        {"query": f"EXT_ID:{pmid}", "format": "json", "resultType": "core"}
    )
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{query}"
    result = {"url": url, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "BioKB-quality-audit/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        matches = [
            item
            for item in payload.get("resultList", {}).get("result", [])
            if item.get("pmid") == pmid
        ]
        if len(matches) != 1:
            result.update({"available": True, "unique_match": False, "hit_count": len(matches)})
            return result
        item = matches[0]
        fields = {key: item.get(key) for key in ("pmid", "pmcid", "doi", "title", "isOpenAccess")}
        checks = {
            key: fields[key] == expected.get(key) for key in ("pmid", "pmcid", "doi", "title")
        }
        checks["open_access_flag"] = fields["isOpenAccess"] == "Y"
        result.update(
            {
                "available": True,
                "unique_match": True,
                "fields": fields,
                "checks": checks,
                "all_identifiers_and_title_match": all(checks.values()),
                "retraction_status": "接口返回字段不足，未判定",
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"available": False, "error": f"{type(exc).__name__}: {exc}"})
    return result


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    return " ".join(re.findall(r"\w+", text, flags=re.UNICODE))


def official_corpus_identifiers(documents: dict) -> dict:
    by_pmid: dict[str, list[dict]] = {}
    for document in documents.values():
        if document["metadata"].get("content_type") not in {"abstract", "fulltext"}:
            continue
        pmid = identifiers(document["metadata"]).get("pmid")
        if pmid:
            by_pmid.setdefault(pmid, []).append(document)
    official: dict[str, list[dict]] = {}
    batches = []
    pmids = sorted(by_pmid)
    for index in range(0, len(pmids), 40):
        batch = pmids[index : index + 40]
        query = " OR ".join(f"EXT_ID:{pmid}" for pmid in batch)
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
            {"query": query, "format": "json", "resultType": "core", "pageSize": 100}
        )
        item = {"requested_pmids": batch, "url": url}
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "BioKB-quality-audit/1.0"})
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.load(response)
            allowed = set(batch)
            for record in payload.get("resultList", {}).get("result", []):
                if record.get("pmid") in allowed:
                    official.setdefault(record["pmid"], []).append(record)
            item.update(
                {
                    "available": True,
                    "reported_hits": payload.get("hitCount"),
                    "matched_pmids": len(set(batch) & official.keys()),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            item.update({"available": False, "error": f"{type(exc).__name__}: {exc}"})
        batches.append(item)
    comparisons = []
    for pmid, local_documents in by_pmid.items():
        matches = official.get(pmid, [])
        for document in local_documents:
            metadata = document["metadata"]
            record = matches[0] if len(matches) == 1 else {}
            comparisons.append(
                {
                    "pmid": pmid,
                    "source_id": metadata.get("source_id"),
                    "content_type": metadata.get("content_type"),
                    "official_unique_match": len(matches) == 1,
                    "local_title": document["title"],
                    "official_title": record.get("title"),
                    "title_exact": bool(record) and document["title"] == record.get("title"),
                    "title_normalized": bool(record)
                    and normalize_title(document["title"])
                    == normalize_title(record.get("title") or ""),
                    "local_year": str(metadata.get("year") or ""),
                    "official_first_publication_date": record.get("firstPublicationDate"),
                    "year_matches_first_publication": bool(record)
                    and str(metadata.get("year") or "")
                    == str(record.get("firstPublicationDate") or "")[:4],
                    "pmcid_matches_when_recorded": not metadata.get("pmcid")
                    or metadata["pmcid"] == record.get("pmcid"),
                    "doi_matches_when_recorded": not metadata.get("doi")
                    or metadata["doi"].lower() == str(record.get("doi") or "").lower(),
                    "official_open_access_flag": record.get("isOpenAccess"),
                }
            )
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Europe PMC REST search resultType=core；未据此判断撤稿或更正状态",
        "batches": batches,
        "distinct_local_pmids": len(by_pmid),
        "eligible_records": len(comparisons),
        "unique_official_pmids": sum(len(official.get(pmid, [])) == 1 for pmid in by_pmid),
        "records_with_unique_match": sum(row["official_unique_match"] for row in comparisons),
        "title_exact_count": sum(row["title_exact"] for row in comparisons),
        "title_normalized_count": sum(row["title_normalized"] for row in comparisons),
        "year_matches_first_publication_count": sum(
            row["year_matches_first_publication"] for row in comparisons
        ),
        "comparisons": comparisons,
    }


def build_report(verify_official: bool) -> tuple[dict, dict, dict]:
    paths = source_paths()
    before = source_hashes(paths)
    database = read_database(ROOT / "data/biokb-v21-semantic/knowledge.sqlite3")
    review_dir = ROOT / "output/upgrade_20261104/answer_review_release"
    fulltext_dir = ROOT / "data/research_corpus/upgrade_20261104/fulltext"
    questions = load_json(review_dir / "fulltext_questions.json")["items"]
    question_by_id = {item["qid"]: item for item in questions}
    answers = load_json(review_dir / "answers.json")
    retrieval = load_json(review_dir / "fulltext_retrieval.json")
    evaluation = load_json(ROOT / "output/upgrade_20261104/experiments/evaluation_semantic.json")
    paper_metrics = load_json(
        ROOT / "output/research_upgrade_20261105/paper_metrics_evaluation.json"
    )
    protocols = load_json(review_dir / "answer_protocol.json")
    acquisition = load_json(fulltext_dir / "acquisition_report.json")
    acquisition_by_pmcid = {item["pmcid"]: item for item in acquisition["downloaded"]}
    source_to_document = {
        document["metadata"].get("source_id"): document
        for document in database["documents"].values()
    }

    fulltexts = {}
    for pmcid in ("PMC12142011", "PMC13199795", "PMC13065327"):
        source_file = fulltext_dir / f"{pmcid}.json"
        source = load_json(source_file)
        document = source_to_document.get(source["source_id"])
        source_meta = document["metadata"] if document else {}
        checks = {
            "xml_matches_acquisition": file_sha256(fulltext_dir / f"{pmcid}.xml")
            == source["acquisition"]["xml_sha256"]
            == acquisition_by_pmcid[pmcid]["xml_sha256"],
            "database_text_matches_source": document is not None
            and document["text"] == source["content"],
            "database_identifiers_match_source": document is not None
            and all(source_meta.get(key) == source.get(key) for key in ("pmid", "pmcid", "doi")),
            "database_license_matches_source": document is not None
            and bool(source.get("license"))
            and source_meta.get("license") == source["license"],
        }
        expected = {key: source[key] for key in ("pmid", "pmcid", "doi", "title")}
        fulltexts[pmcid] = {
            "source_id": source["source_id"],
            "source_json_sha256": file_sha256(source_file),
            "source_xml_sha256": file_sha256(fulltext_dir / f"{pmcid}.xml"),
            "license_recorded": bool(source.get("license")),
            "checks": checks,
            "official": official_record(source["pmid"], expected) if verify_official else None,
        }

    retrieval_rows = {
        item["qid"]: item for item in retrieval["results"]["hybrid_rerank"]["details"]
    }
    cases = [
        (
            f"{item['qid']}@historical",
            item["qid"],
            item["origin"],
            item["question"],
            item["result"],
            item["review"],
        )
        for item in answers["items"]
    ]
    for filename in ("replay_fulltext03.json", "replay_fulltext04_v2.json"):
        replay = load_json(ROOT / "output/research_upgrade_20261105" / filename)
        cases.append(
            (
                f"{replay['case_id']}@replay",
                replay["case_id"],
                "限定论文后的已知失败题复测",
                replay["original_question"],
                replay["current"],
                {"status": replay["human_review_status"]},
            )
        )
    review_items = []
    for case_id, qid, origin, question, answer, review in cases:
        specification = question_by_id.get(qid)
        citation_checks = [
            check_citation(citation, answer["answer"], database)
            for citation in answer.get("citations", [])
        ]
        row = {
            "case_id": case_id,
            "qid": qid,
            "origin": origin,
            "question": question,
            "reference_answer_draft": specification["reference_answer_draft"]
            if specification
            else None,
            "answer": answer["answer"],
            "mode": answer["mode"],
            "model": answer.get("model"),
            "generation_fingerprint": answer.get("generation_fingerprint"),
            "citation_integrity": all(item["passed"] for item in citation_checks),
            "citations": citation_checks,
            "source_review_status": review.get("status"),
            "human_review": {
                "reviewer": None,
                "correctness_0_2": None,
                "evidence_support_0_2": None,
                "coverage_0_2": None,
                "abstention_appropriate": None,
            },
        }
        if specification:
            pmcid = specification["gold_doc_ids"][0].split("-")[0]
            source = load_json(fulltext_dir / f"{pmcid}.json")
            quote = specification["evidence_quote"]
            start = source["content"].find(quote)
            end = start + len(quote)
            ranked_candidates = retrieval_rows[qid]["evidence"]
            intervals = []
            matching_ranks = []
            for rank, candidate in enumerate(ranked_candidates, 1):
                chunk = database["chunks"].get(candidate["chunk_id"])
                if chunk and candidate["source_id"] == specification["gold_doc_ids"][0]:
                    intervals.append((rank, chunk["start"], chunk["end"]))
                    if start >= 0 and chunk["start"] < end and chunk["end"] > start:
                        matching_ranks.append(rank)
            used_intervals = [
                (item["start_offset"], item["end_offset"])
                for item in citation_checks
                if item["used"] and item["source_id"] == specification["gold_doc_ids"][0]
            ]
            row["original_fulltext"] = {
                "source_id": specification["gold_doc_ids"][0],
                "json_sha256_matches_frozen_question": fulltexts[pmcid]["source_json_sha256"]
                == specification["source_file_sha256"],
                "quote_found_exactly": start >= 0,
                "quote_start_offset": start,
                "quote_end_offset": end if start >= 0 else None,
                "quote": quote,
                "candidate_ranks_overlapping_quote": matching_ranks,
                "top_5_quote_overlap_chars": overlap_length(
                    start, end, [(a, b) for rank, a, b in intervals if rank <= 5]
                )
                if start >= 0
                else None,
                "used_citation_quote_overlap_chars": overlap_length(start, end, used_intervals)
                if start >= 0
                else None,
            }
        review_items.append(row)

    eligible = [
        document["metadata"]
        for document in database["documents"].values()
        if document["metadata"].get("content_type") in {"abstract", "fulltext"}
    ]
    catalog_records = [
        {k: document[k] for k in ("id", "title", "filename", "metadata")}
        for document in database["documents"].values()
    ]
    catalog = build_catalog(catalog_records)
    all_citations = [citation for item in review_items for citation in item["citations"]]
    after = source_hashes(paths)
    summary = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "只读语料与冻结问答的机械一致性核对；不能替代领域专家的事实判断",
        "source_hashes_before": before,
        "source_hashes_after": after,
        "sources_unchanged": before == after,
        "index_revision": database["revision"],
        "embedding_fingerprints": database["vector_counts"],
        "document_count": len(database["documents"]),
        "chunk_count": len(database["chunks"]),
        "paper_summary": catalog["summary"],
        "eligible_metadata": {
            "records": len(eligible),
            "by_content_type": dict(Counter(meta.get("content_type") for meta in eligible)),
            "by_quality_status": dict(
                Counter((meta.get("quality") or {}).get("status") for meta in eligible)
            ),
            "field_present": {
                field: sum(bool(meta.get(field)) for meta in eligible)
                for field in (
                    "source_id",
                    "url",
                    "license",
                    "pmid",
                    "pmcid",
                    "doi",
                    "year",
                    "journal",
                    "authors",
                    "topic",
                )
            },
            "identifiable_pmid": sum("pmid" in identifiers(meta) for meta in eligible),
            "distinct_topics": sorted({meta["topic"] for meta in eligible if meta.get("topic")}),
        },
        "fulltexts": fulltexts,
        "official_corpus_identifiers": official_corpus_identifiers(database["documents"])
        if verify_official
        else None,
        "frozen_evaluation": {
            "dataset_sha256": paper_metrics["dataset_sha256"],
            "corpus_sha256": paper_metrics["corpus"]["sha256"],
            "dataset_origin": paper_metrics["dataset_origin"],
            "human_review_status": paper_metrics["human_review_status"],
            "question_count": paper_metrics["results"]["hybrid_rerank"]["n"],
            "reranker_fingerprint": paper_metrics["reranker_fingerprint"],
            "consistency_checks": {
                "dataset_matches_frozen_report": paper_metrics["dataset_sha256"]
                == evaluation["dataset_sha256"],
                "corpus_matches_frozen_report": paper_metrics["corpus"]["sha256"]
                == evaluation["corpus"]["sha256"],
                "index_matches_current": str(paper_metrics["index_revision"])
                == str(evaluation["index_revision"])
                == str(database["revision"]),
                "embedding_matches_current": paper_metrics["embedding_fingerprint"]
                == evaluation["embedding_fingerprint"]
                and paper_metrics["embedding_fingerprint"] in database["vector_counts"],
                "reranker_matches_frozen_report": paper_metrics["reranker_fingerprint"]
                == evaluation["reranker_fingerprint"],
                "source_report_hash_matches": file_sha256(
                    ROOT / "output/upgrade_20261104/experiments/evaluation_semantic.json"
                )
                == paper_metrics["source_report_sha256"],
            },
        },
        "fulltext_retrieval": {
            "question_count": retrieval["results"]["hybrid_rerank"]["n"],
            "document_hit_at_10": retrieval["results"]["hybrid_rerank"]["Hit@10"],
            "dataset_sha256": retrieval["dataset_sha256"],
            "index_matches_current": str(retrieval["index_revision"]) == str(database["revision"]),
            "embedding_matches_current": retrieval["embedding_fingerprint"]
            in database["vector_counts"],
            "question_origin": retrieval["dataset_origin"],
            "question_review_status": retrieval["human_review_status"],
            "reference_quote_overlap_in_top_5": sum(
                bool(item.get("original_fulltext", {}).get("top_5_quote_overlap_chars"))
                for item in review_items
                if item["case_id"].endswith("@historical") and item["qid"].startswith("fulltext-")
            ),
        },
        "answer_snapshots": {
            "historical_cases": len(answers["items"]),
            "replay_cases": len(cases) - len(answers["items"]),
            "historical_review_status": answers["human_review_status"],
            "generation_fingerprints": sorted(
                {item["result"].get("generation_fingerprint") for item in answers["items"]}
            ),
            "citations_total": len(all_citations),
            "citations_integrity_passed": sum(item["passed"] for item in all_citations),
            "failed_citations": [
                {"case_id": row["case_id"], "number": item["number"], "checks": item["checks"]}
                for row in review_items
                for item in row["citations"]
                if not item["passed"]
            ],
        },
        "protocol_cases": [
            {
                "kind": item["kind"],
                "response": item["response"],
                "format_validation": item["format_validation"],
                "human_review_status": item["review"]["status"],
            }
            for item in protocols["items"]
        ],
    }
    evidence = {
        "notice": "引用定位与原文一致只证明追溯链完整；参考答案是 Codex 草稿，人工评分均为空",
        "items": review_items,
    }
    return summary, evidence, {"items": review_items}


def render_review(items: list[dict]) -> str:
    lines = [
        "# 冻结问答逐题证据核对包",
        "",
        "这里保存历史问答与两道已知失败题复测的原文定位。参考答案是草稿，引用一致性是机械检查；所有科研判断待真实领域评审者填写。完整引用文本见同目录 `evidence_review.json`。",
        "",
    ]
    for item in items:
        lines.extend(
            [
                f"## {item['case_id']}",
                "",
                f"- 问题：{item['question']}",
                f"- 参考答案草稿：{item['reference_answer_draft'] or '无'}",
                f"- 模型回答（{item['mode']}）：{item['answer']}",
                f"- 引用定位机械一致：{'是' if item['citation_integrity'] else '否'}；人工复核：待评审",
                "",
            ]
        )
        original = item.get("original_fulltext")
        if original:
            lines.extend(
                [
                    f"原始全文 `{original['source_id']}` 字符 {original['quote_start_offset']}–{original['quote_end_offset']}；完整原文摘录：",
                    "",
                    "```text",
                    original["quote"].strip(),
                    "```",
                    "",
                    f"候选片段与该摘录相交的排名：{original['candidate_ranks_overlapping_quote']}；前 5 候选覆盖 {original['top_5_quote_overlap_chars']} / {len(original['quote'])} 字符；实际使用引用覆盖 {original['used_citation_quote_overlap_chars']} / {len(original['quote'])} 字符。相交只表示位置关系，不等于事实支持。",
                    "",
                ]
            )
        lines.extend(
            [
                "| 编号 | 用于回答 | source_id | chunk_id | 原文字符偏移 | 定位核对 |",
                "|---:|:---:|---|---|---:|:---:|",
            ]
        )
        for citation in item["citations"]:
            lines.append(
                f"| {citation['number']} | {'是' if citation['used'] else '否'} | "
                f"`{citation['source_id']}` | `{citation['chunk_id']}` | "
                f"{citation['start_offset']}–{citation['end_offset']} | "
                f"{'通过' if citation['passed'] else '失败'} |"
            )
        lines.append("")
        for citation in item["citations"]:
            if citation["used"]:
                lines.extend(
                    [
                        f"引用 [{citation['number']}] 的冻结证据文本：",
                        "",
                        "```text",
                        citation["text"].strip(),
                        "```",
                        "",
                    ]
                )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-official", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "output").resolve()):
        parser.error("审计材料只能写入工作区 output 目录")
    summary, evidence, review = build_report(args.verify_official)
    mechanical_checks = {
        "input_files_unchanged": summary["sources_unchanged"],
        "citations_traceable": summary["answer_snapshots"]["citations_integrity_passed"]
        == summary["answer_snapshots"]["citations_total"],
        "fulltexts_traceable": all(
            all(item["checks"].values()) for item in summary["fulltexts"].values()
        ),
        "frozen_experiment_consistent": all(
            summary["frozen_evaluation"]["consistency_checks"].values()
        ),
        "review_quotes_traceable": all(
            item["original_fulltext"]["quote_found_exactly"]
            and item["original_fulltext"]["json_sha256_matches_frozen_question"]
            for item in review["items"]
            if "original_fulltext" in item
        ),
    }
    if args.verify_official:
        official = summary["official_corpus_identifiers"]
        mechanical_checks["official_identifiers_traceable"] = (
            all(batch["available"] for batch in official["batches"])
            and official["unique_official_pmids"] == official["distinct_local_pmids"]
            and official["title_normalized_count"] == official["eligible_records"]
            and official["year_matches_first_publication_count"] == official["eligible_records"]
            and all(
                item["official"]["all_identifiers_and_title_match"]
                for item in summary["fulltexts"].values()
            )
        )
    summary["mechanical_checks"] = mechanical_checks
    summary["mechanical_audit_passed"] = all(mechanical_checks.values())
    output.mkdir(parents=True, exist_ok=True)
    (output / "quality_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "evidence_review.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "逐题证据核对.md").write_text(render_review(review["items"]), encoding="utf-8")
    with (output / "人工复核表_空白.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=(
                "case_id",
                "qid",
                "问题",
                "评审者",
                "评审时间",
                "正确性0至2",
                "证据支持0至2",
                "完整性0至2",
                "拒答适当",
                "无支持论断",
                "评审依据与备注",
            ),
        )
        writer.writeheader()
        for item in review["items"]:
            writer.writerow(
                {"case_id": item["case_id"], "qid": item["qid"], "问题": item["question"]}
            )
    print(
        json.dumps(
            {
                "sources_unchanged": summary["sources_unchanged"],
                "citations_integrity_passed": summary["answer_snapshots"][
                    "citations_integrity_passed"
                ],
                "citations_total": summary["answer_snapshots"]["citations_total"],
                "paper_summary": summary["paper_summary"],
                "mechanical_audit_passed": summary["mechanical_audit_passed"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["mechanical_audit_passed"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
