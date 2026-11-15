"""为开放全文批次保存 PubMed XML 和 PMC 标识转换响应并核对文献身份。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from scripts.expand_open_corpus import (
    file_sha256,
    request,
    save_bytes_atomic,
    save_json_atomic,
    utc_now,
)
from scripts.verify_pubmed_snapshot import parse_records
from scripts.verify_pubmed_snapshot import verify as verify_snapshot

EFETCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ID_CONVERTER_API = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"


def fetch(batch: Path) -> dict:
    batch = batch.resolve(strict=True)
    acquisition = json.loads((batch / "acquisition_report.json").read_text(encoding="utf-8"))
    items = acquisition["items"]
    if not items or len({item["pmid"] for item in items}) != len(items):
        raise ValueError("采集批次没有唯一的 PMID 清单")
    xml_path = batch / "pubmed_metadata.xml"
    conversion_path = batch / "id_conversion_response.json"
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-research-corpus/2.4 (academic project)"
        if not xml_path.exists():
            response = request(
                session,
                EFETCH_API,
                {"db": "pubmed", "id": ",".join(item["pmid"] for item in items), "retmode": "xml"},
            )
            save_bytes_atomic(xml_path, response.content)
        if not conversion_path.exists():
            response = request(
                session,
                ID_CONVERTER_API,
                {
                    "ids": ",".join(item["pmcid"] for item in items),
                    "format": "json",
                    "tool": "BioKB",
                },
            )
            save_bytes_atomic(conversion_path, response.content)
    records = parse_records(xml_path)
    expected = {item["pmid"]: item for item in items}
    pubmed_errors = []
    if set(records) != set(expected):
        pubmed_errors.append("PubMed EFetch 返回的 PMID 集合与采集批次不一致")
    for pmid, item in expected.items():
        record = records.get(pmid)
        if record and (
            record["pmcid"] != item["pmcid"]
            or record["doi"].lower() != item["doi"].lower()
        ):
            pubmed_errors.append(f"PubMed 与全文标识不一致：{pmid}")
    reviews = sum("Review" in record["publication_types"] for record in records.values())
    retractions = [pmid for pmid, record in records.items() if record["retraction_flag"]]
    pubmed_report = {
        "checked_at_utc": utc_now(),
        "source": EFETCH_API,
        "xml_sha256": file_sha256(xml_path),
        "acquisition_report_sha256": file_sha256(batch / "acquisition_report.json"),
        "checked": len(records),
        "passed": not pubmed_errors,
        "review_count": reviews,
        "retraction_flags": retractions,
        "records": records,
        "errors": pubmed_errors,
    }
    save_json_atomic(batch / "pubmed_verification.json", pubmed_report)

    conversion = json.loads(conversion_path.read_text(encoding="utf-8"))
    by_pmcid = {record.get("pmcid"): record for record in conversion.get("records", [])}
    checks = []
    for item in items:
        record = by_pmcid.get(item["pmcid"], {})
        checks.append(
            {
                "pmcid": item["pmcid"],
                "pmid_matches": str(record.get("pmid") or "") == item["pmid"],
                "doi_matches": str(record.get("doi") or "").lower() == item["doi"].lower(),
                "error": record.get("errmsg", ""),
            }
        )
    converter_report = {
        "checked_at_utc": utc_now(),
        "service": ID_CONVERTER_API,
        "response_sha256": file_sha256(conversion_path),
        "acquisition_report_sha256": file_sha256(batch / "acquisition_report.json"),
        "checked": len(checks),
        "passed": (
            conversion.get("status") == "ok"
            and len(by_pmcid) == len(items)
            and all(row["pmid_matches"] and row["doi_matches"] for row in checks)
        ),
        "checks": checks,
    }
    save_json_atomic(batch / "id_conversion_verification.json", converter_report)
    snapshot = verify_snapshot(batch)
    return {
        "pubmed_passed": pubmed_report["passed"],
        "id_conversion_passed": converter_report["passed"],
        "snapshot_passed": snapshot["passed"],
        "checked": len(items),
        "reviews": reviews,
        "retraction_flags": retractions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    result = fetch(parser.parse_args().batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(result[key] for key in ("pubmed_passed", "id_conversion_passed", "snapshot_passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
