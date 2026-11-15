"""复核采集批次并生成可入库的主题文献副本，保留采集原件。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import requests

from scripts.expand_open_corpus import (
    API,
    article_from_fulltext,
    clean_text,
    current_corpus,
    file_sha256,
    lookup_current,
    parse_fulltext,
    publication_types,
    save_json_atomic,
    topic_relevant,
    utc_now,
    valid_candidate,
    verify_batch,
)


def curate(batch: Path, decisions_path: Path) -> dict:
    batch = batch.resolve(strict=True)
    source_report = batch / "acquisition_report.json"
    report = json.loads(source_report.read_text(encoding="utf-8"))
    database = Path(report["database"])
    raw_dir = batch / "raw_xml"
    document_dir = batch / "documents"
    curated_dir = batch / "curated_documents"
    verify_batch(report, database, raw_dir, document_dir)
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    exclusions = decisions.get("exclude_pmcids", {})
    if not isinstance(exclusions, dict) or any(
        not isinstance(pmcid, str) or not isinstance(reason, str) or not reason.strip()
        for pmcid, reason in exclusions.items()
    ):
        raise ValueError("主题排除清单格式不正确")
    known_pmcids = {item["pmcid"] for item in report["items"]}
    if set(exclusions) - known_pmcids:
        raise ValueError("主题排除清单包含未采集的 PMCID")
    _, current_pmids, revision = current_corpus(database)
    if report["source_revision"] != revision:
        raise ValueError("父库修订号与采集时不一致")

    if report.get("metadata_source") == "europe_pmc_fulltext_xml":
        pubmed_path = batch / "pubmed_verification.json"
        pubmed = json.loads(pubmed_path.read_text(encoding="utf-8"))
        if not pubmed["passed"] or pubmed["xml_sha256"] != file_sha256(
            batch / "pubmed_metadata.xml"
        ):
            raise ValueError("PubMed 元数据快照未通过核验")
        official = {}
        for item in report["items"]:
            article = article_from_fulltext((raw_dir / f"{item['pmcid']}.xml").read_bytes())
            if article["pmid"] != item["pmid"] or article["pmcid"] != item["pmcid"]:
                raise ValueError(f"官方 XML 与采集标识不一致：{item['pmcid']}")
            pubmed_record = pubmed["records"].get(item["pmid"])
            if (
                not pubmed_record
                or pubmed_record["pmcid"] != item["pmcid"]
                or pubmed_record["doi"].lower() != item["doi"].lower()
            ):
                raise ValueError(f"PubMed 标识与 Europe PMC 全文不一致：{item['pmcid']}")
            article["pubTypeList"]["pubType"] = list(
                dict.fromkeys([*publication_types(article), *pubmed_record["publication_types"]])
            )
            if pubmed_record["retraction_flag"]:
                exclusions.setdefault(item["pmcid"], "PubMed 标记撤稿或关注声明")
            official[item["pmid"]] = article
    else:
        with requests.Session() as session:
            session.headers["User-Agent"] = "BioKB-corpus-curation/2.2 (+local research project)"
            official = lookup_current(session, [item["pmid"] for item in report["items"]])
    if len(official) != len(report["items"]):
        raise ValueError("有文献未找到唯一的 Europe PMC 官方记录")

    entries = []
    curated_payloads = {}
    for item in report["items"]:
        pmid, pmcid = item["pmid"], item["pmcid"]
        article = official[pmid]
        if not valid_candidate(article) or str(article["pmcid"]) != pmcid:
            raise ValueError(f"官方标识或开放访问状态不一致：{pmcid}")
        original_path = document_dir / f"{pmcid}.json"
        document = json.loads(original_path.read_text(encoding="utf-8"))
        raw_xml = (raw_dir / f"{pmcid}.xml").read_bytes()
        content, _, license_url = parse_fulltext(raw_xml, article)
        expected_origin = "existing_abstract" if pmid in current_pmids else "new_paper"
        if (
            item["origin"] != expected_origin
            or document["pmid"] != pmid
            or document["pmcid"] != pmcid
            or document["source_id"] != item["source_id"]
            or clean_text(document["title"]) != clean_text(article["title"])
            or document["content"] != content
            or document["license"] != license_url
            or item["license_url"] != license_url
            or item["content_characters"] != len(content)
            or document.get("acquisition", {}).get("xml_sha256") != item["xml_sha256"]
            or str(document["doi"]).lower() != str(article.get("doi") or "").lower()
            or str(document["year"]) != str(article["pubYear"])
        ):
            raise ValueError(f"采集 JSON 与官方记录或原始 XML 不一致：{pmcid}")
        topic_match = topic_relevant(article, item["topic"])
        exclusion = exclusions.get(pmcid, "")
        if not topic_match and not exclusion:
            exclusion = "题名及摘要未覆盖所分配的主题"
        curated = dict(document)
        curated["title"] = clean_text(article["title"])
        curated["publication_types"] = publication_types(article)
        curated["xml_sha256"] = item["xml_sha256"]
        entry = {
            "pmid": pmid,
            "pmcid": pmcid,
            "doi": document["doi"],
            "title": curated["title"],
            "topic": item["topic"],
            "origin": item["origin"],
            "publication_types": curated["publication_types"],
            "license_url": license_url,
            "topic_keyword_match": topic_match,
            "included": not bool(exclusion),
            "exclusion_reason": exclusion,
            "source_json_sha256": item["json_sha256"],
            "source_xml_sha256": item["xml_sha256"],
            "official_api_url": (
                f"{API}/{pmcid}/fullTextXML"
                if report.get("metadata_source") == "europe_pmc_fulltext_xml"
                else f"{API}/search?query=EXT_ID%3A{pmid}%20AND%20SRC%3AMED&format=json&resultType=core"
            ),
        }
        entries.append(entry)
        if not exclusion:
            curated_payloads[pmcid] = curated

    curated_dir.mkdir(exist_ok=True)
    expected_files = {f"{pmcid}.json" for pmcid in curated_payloads}
    if {path.name for path in curated_dir.iterdir()} - expected_files:
        raise ValueError("待入库目录存在未登记文件，需先核对")
    for entry in entries:
        if not entry["included"]:
            continue
        target = curated_dir / f"{entry['pmcid']}.json"
        payload = curated_payloads[entry["pmcid"]]
        if target.exists():
            if json.loads(target.read_text(encoding="utf-8")) != payload:
                raise ValueError(f"待入库文件已有不同内容：{target.name}")
        else:
            save_json_atomic(target, payload)
        entry["curated_json_sha256"] = file_sha256(target)

    included = [entry for entry in entries if entry["included"]]
    result = {
        "curated_at_utc": utc_now(),
        "status": "仅完成来源、许可、标识和主题初筛；学术正确性未由领域专家审核",
        "source_acquisition_report_sha256": file_sha256(source_report),
        "source_database_sha256": file_sha256(database),
        "source_revision": revision,
        "decisions_sha256": file_sha256(decisions_path),
        "pubmed_verification_sha256": (
            file_sha256(batch / "pubmed_verification.json")
            if report.get("metadata_source") == "europe_pmc_fulltext_xml"
            else None
        ),
        "counts": {
            "acquired": len(entries),
            "included": len(included),
            "excluded": len(entries) - len(included),
            "new_papers": sum(entry["origin"] == "new_paper" for entry in included),
            "existing_abstract_upgrades": sum(
                entry["origin"] == "existing_abstract" for entry in included
            ),
            "reviews": sum(
                any("review" in value.lower() for value in entry["publication_types"])
                for entry in included
            ),
            "by_topic": dict(sorted(Counter(entry["topic"] for entry in included).items())),
        },
        "entries": entries,
    }
    save_json_atomic(batch / "curation_report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    args = parser.parse_args()
    result = curate(args.batch, args.decisions)
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
