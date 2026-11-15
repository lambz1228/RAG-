"""按已保存的候选 PMCID 清单采集历史开放全文，不依赖检索服务运行状态。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import requests

from scripts.expand_open_corpus import (
    API,
    ROOT,
    TOPICS,
    accepted_count,
    article_from_fulltext,
    current_corpus,
    file_sha256,
    make_document,
    parse_fulltext,
    request,
    save_bytes_atomic,
    save_json_atomic,
    sha256_bytes,
    topic_relevant,
    utc_now,
    valid_candidate,
    verify_batch,
)


def validate_seeds(seed_data: dict) -> list[dict]:
    """拒绝重复配额、异常 ID 和缺少检索出处的候选清单。"""
    groups = seed_data.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("候选清单没有有效分组")
    topics = {topic for topic, _ in TOPICS}
    keys = set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("候选分组格式不正确")
        key = (group.get("topic"), group.get("year"))
        if key in keys or key[0] not in topics or not isinstance(key[1], int):
            raise ValueError("候选清单存在重复或无效的主题年份")
        keys.add(key)
        if not isinstance(group.get("q"), str) or not group["q"].strip():
            raise ValueError("候选分组缺少检索词")
        ids = group.get("ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(
                not isinstance(value, str) or not re.fullmatch(r"PMC[1-9][0-9]*", value)
                for value in ids
            )
        ):
            raise ValueError("候选分组包含无效 PMCID")
    return groups


def collect(database: Path, seed_path: Path, output: Path, per_group: int = 1) -> dict:
    database = database.resolve(strict=True)
    seed_path = seed_path.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to((ROOT / "data/research_corpus").resolve()):
        raise ValueError("采集目录必须位于 data/research_corpus 内")
    if seed_path.parent != output:
        raise ValueError("候选清单必须保存在本批次目录")
    seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
    groups = validate_seeds(seed_data)
    max_attempts = seed_data.get("max_attempts_per_group", 15)
    if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 40:
        raise ValueError("每组最多尝试次数应为 1–40")
    discovery_source = seed_data.get(
        "discovery_source", "PMC article candidate search; XML verified through Europe PMC REST"
    )
    if not isinstance(discovery_source, str) or not discovery_source.strip():
        raise ValueError("候选清单缺少有效发现来源")
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw_xml"
    document_dir = output / "documents"
    raw_dir.mkdir(exist_ok=True)
    document_dir.mkdir(exist_ok=True)
    report_path = output / "acquisition_report.json"
    report = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else {
            "created_at_utc": utc_now(),
            "database": str(database),
            "database_sha256_before": file_sha256(database),
            "source_revision": current_corpus(database)[2],
            "metadata_source": "europe_pmc_fulltext_xml",
            "discovery_source": discovery_source,
            "seed_manifest_sha256": file_sha256(seed_path),
            "per_group": per_group,
            "max_attempts_per_group": max_attempts,
            "items": [],
            "failures": [],
            "searches": [
                {
                    "topic": group["topic"],
                    "year": group["year"],
                    "query": group["q"],
                    "candidate_count": len(group["ids"]),
                    "source_url": group.get("source_url", ""),
                }
                for group in groups
            ],
        }
    )
    verify_batch(report, database, raw_dir, document_dir)
    if (
        report["seed_manifest_sha256"] != file_sha256(seed_path)
        or report["per_group"] != per_group
        or report.get("max_attempts_per_group", 15) != max_attempts
        or report["discovery_source"] != discovery_source
    ):
        raise ValueError("续跑时候选清单或分组配额发生变化")
    _, current_pmids, revision = current_corpus(database)
    if report["source_revision"] != revision:
        raise ValueError("续跑时源库修订号发生变化")
    selected = {item["pmcid"] for item in report["items"]}
    failed = {
        (item.get("topic"), item.get("year"), item.get("pmcid"))
        for item in report["failures"]
        if item.get("pmcid") and not item.get("retryable", False)
    }
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-historical-corpus/2.3 (+local research project)"
        for group in groups:
            topic, year = group["topic"], group["year"]
            accepted = accepted_count(report["items"], topic, "new_paper", year)
            attempts = 0
            for pmcid in group["ids"]:
                if accepted >= per_group or attempts >= max_attempts:
                    break
                if pmcid in selected or (topic, year, pmcid) in failed:
                    continue
                attempts += 1
                try:
                    response = request(session, API + f"/{pmcid}/fullTextXML")
                    raw = response.content
                    article = article_from_fulltext(raw)
                    if article["pmcid"] != pmcid:
                        raise ValueError("候选 PMCID 与官方 XML 不一致")
                    if str(article["pubYear"]) != str(year):
                        raise ValueError("官方 XML 发表年份与目标年份不一致")
                    if not valid_candidate(article) or not topic_relevant(article, topic):
                        raise ValueError("摘要、文献类型或主题初筛不通过")
                    if article["pmid"] in current_pmids:
                        raise ValueError("父库已有该 PMID")
                    content, _, license_url = parse_fulltext(raw, article)
                    record = make_document(article, topic, content, license_url, sha256_bytes(raw))
                    record["acquisition"].update(
                        {
                            "metadata_source": "europe_pmc_fulltext_xml",
                            "discovery_query": group["q"],
                            "discovery_url": group.get("source_url")
                            or f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
                        }
                    )
                    json_path = document_dir / f"{pmcid}.json"
                    xml_path = raw_dir / f"{pmcid}.xml"
                    if json_path.exists() or xml_path.exists():
                        raise ValueError("本批次已有同名原文，需核对中断位置")
                    try:
                        save_bytes_atomic(xml_path, raw)
                        save_json_atomic(json_path, record)
                    except OSError:
                        xml_path.unlink(missing_ok=True)
                        json_path.unlink(missing_ok=True)
                        raise
                    entry = {
                        "pmid": article["pmid"],
                        "pmcid": pmcid,
                        "doi": record["doi"],
                        "title": record["title"],
                        "topic": topic,
                        "year": record["year"],
                        "origin": "new_paper",
                        "source_id": record["source_id"],
                        "license_url": license_url,
                        "publication_types": record["publication_types"],
                        "content_characters": len(content),
                        "xml_sha256": sha256_bytes(raw),
                        "json_sha256": file_sha256(json_path),
                        "article_url": record["url"],
                    }
                    report["items"].append(entry)
                    selected.add(pmcid)
                    accepted += 1
                    save_json_atomic(report_path, report)
                    print(f"[{topic}/{year}] 已收录 {pmcid}（{accepted}/{per_group}）", flush=True)
                except (requests.RequestException, ValueError, ET.ParseError) as exc:
                    report["failures"].append(
                        {
                            "topic": topic,
                            "year": year,
                            "pmcid": pmcid,
                            "reason": str(exc)
                            if isinstance(exc, ValueError)
                            else "download_failed",
                            "error_type": type(exc).__name__,
                            "retryable": isinstance(exc, requests.RequestException),
                        }
                    )
                    if not isinstance(exc, requests.RequestException):
                        failed.add((topic, year, pmcid))
                    save_json_atomic(report_path, report)
            print(f"[{topic}/{year}] 新论文：{accepted}/{per_group}", flush=True)
    report["completed_at_utc"] = utc_now()
    report["database_sha256_after"] = file_sha256(database)
    report["source_unchanged"] = report["database_sha256_before"] == report["database_sha256_after"]
    report["counts"] = {
        "fulltexts": len(report["items"]),
        "new_papers": len(report["items"]),
        "by_topic": dict(sorted(Counter(item["topic"] for item in report["items"]).items())),
        "by_year": dict(sorted(Counter(item["year"] for item in report["items"]).items())),
        "failures": len(report["failures"]),
        "groups_filled": sum(
            accepted_count(report["items"], group["topic"], "new_paper", group["year"]) >= per_group
            for group in groups
        ),
        "groups_total": len(groups),
    }
    save_json_atomic(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-group", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.per_group <= 5:
        parser.error("每组采集篇数应为 1–5")
    result = collect(args.database, args.seeds, args.output, args.per_group)
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
    return 0 if result["source_unchanged"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
