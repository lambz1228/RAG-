"""从 PubMed 原始 XML 重新核对历史批次的文献标识与类型快照。"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.expand_open_corpus import file_sha256, save_json_atomic, utc_now

RETRACTION_TYPES = {
    "Retracted Publication",
    "Retraction of Publication",
    "Expression of Concern",
}
RETRACTION_REFS = {
    "RetractionIn",
    "RetractionOf",
    "ExpressionOfConcernIn",
    "ExpressionOfConcernFor",
}


def parse_records(xml_path: Path) -> dict[str, dict]:
    root = ET.parse(xml_path).getroot()
    records = {}
    for article in root.findall("./PubmedArticle"):
        pmid = article.findtext("./MedlineCitation/PMID", default="").strip()
        ids = article.findall("./PubmedData/ArticleIdList/ArticleId")

        def unique_id(kind: str) -> str:
            values = {
                (node.text or "").strip()
                for node in ids
                if node.get("IdType") == kind and (node.text or "").strip()
            }
            if len(values) != 1:
                raise ValueError(f"PubMed {pmid} 缺少唯一的 {kind} 标识")
            return values.pop()

        if not pmid.isdigit() or pmid in records:
            raise ValueError(f"PubMed XML 中 PMID 缺失或重复：{pmid}")
        publication_types = [
            (node.text or "").strip()
            for node in article.findall("./MedlineCitation/Article/PublicationTypeList/PublicationType")
        ]
        related_ref_types = [
            node.get("RefType", "")
            for node in article.findall("./MedlineCitation/CommentsCorrectionsList/CommentsCorrections")
        ]
        records[pmid] = {
            "pmid": pmid,
            "pmcid": unique_id("pmc"),
            "doi": unique_id("doi"),
            "publication_types": publication_types,
            "related_ref_types": related_ref_types,
            "retraction_flag": bool(
                RETRACTION_TYPES.intersection(publication_types)
                or RETRACTION_REFS.intersection(related_ref_types)
            ),
        }
    if not records:
        raise ValueError("PubMed XML 中没有文献记录")
    return records


def verify(batch: Path) -> dict:
    batch = batch.resolve(strict=True)
    xml_path = batch / "pubmed_metadata.xml"
    snapshot = json.loads((batch / "pubmed_verification.json").read_text(encoding="utf-8"))
    acquisition = json.loads((batch / "acquisition_report.json").read_text(encoding="utf-8"))
    parsed = parse_records(xml_path)
    expected = snapshot.get("records", {})
    errors = []
    if file_sha256(xml_path) != snapshot.get("xml_sha256"):
        errors.append("PubMed XML 摘要与快照不一致")
    if set(parsed) != set(expected) or set(parsed) != {
        item["pmid"] for item in acquisition["items"]
    }:
        errors.append("PubMed XML、快照与采集清单的 PMID 集合不一致")
    for pmid, record in parsed.items():
        if record != expected.get(pmid):
            errors.append(f"PubMed 原始 XML 与快照字段不一致：{pmid}")
    for item in acquisition["items"]:
        record = parsed.get(item["pmid"])
        if record and (
            record["pmcid"] != item["pmcid"]
            or record["doi"].lower() != item["doi"].lower()
        ):
            errors.append(f"PubMed XML 与采集清单标识不一致：{item['pmid']}")
    reviews = sum("Review" in record["publication_types"] for record in parsed.values())
    retractions = [pmid for pmid, record in parsed.items() if record["retraction_flag"]]
    if snapshot.get("checked") != len(parsed) or snapshot.get("review_count") != reviews:
        errors.append("PubMed 快照数量或 Review 类型计数不一致")
    if set(snapshot.get("retraction_flags", [])) != set(retractions):
        errors.append("PubMed 快照撤稿或关注标记不一致")
    result = {
        "verified_at_utc": utc_now(),
        "pubmed_xml_sha256": file_sha256(xml_path),
        "pubmed_snapshot_sha256": file_sha256(batch / "pubmed_verification.json"),
        "acquisition_report_sha256": file_sha256(batch / "acquisition_report.json"),
        "checked": len(parsed),
        "reviews": reviews,
        "retraction_flags": retractions,
        "related_records": [
            {
                "pmid": pmid,
                "pmcid": record["pmcid"],
                "related_ref_types": record["related_ref_types"],
            }
            for pmid, record in parsed.items()
            if record["related_ref_types"]
        ],
        "errors": errors,
        "passed": not errors,
        "human_academic_review": "pending",
    }
    save_json_atomic(batch / "pubmed_snapshot_audit.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
