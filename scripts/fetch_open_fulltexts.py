"""为当前摘要检索少量开放全文，原始响应独立留存，不改写已有文献。"""

import argparse
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def fetch(database: Path, output: Path, limit: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        metadata = [json.loads(row[0]) for row in conn.execute("SELECT metadata FROM documents")]
    records, failures = [], []
    session = requests.Session()
    session.headers["User-Agent"] = "BioKB-research/2.1"
    for meta in metadata:
        if len(records) >= limit:
            break
        if meta.get("source") != "europe_pmc":
            continue
        pmid = str(meta.get("id", ""))
        if not pmid.isdigit():
            continue
        try:
            response = session.get(
                API + "/search",
                params={
                    "query": f"EXT_ID:{pmid} AND SRC:MED",
                    "format": "json",
                    "resultType": "core",
                },
                timeout=20,
            )
            response.raise_for_status()
            hits = response.json().get("resultList", {}).get("result", [])
            article = next(
                (
                    row
                    for row in hits
                    if str(row.get("id")) == pmid
                    and row.get("isOpenAccess") == "Y"
                    and row.get("pmcid")
                ),
                None,
            )
            if article is None:
                continue
            pmcid = article["pmcid"]
            xml = session.get(API + f"/{pmcid}/fullTextXML", timeout=30)
            xml.raise_for_status()
            if len(xml.content) > 10 * 1024 * 1024:
                raise ValueError("全文 XML 超过 10 MB")
            root = ET.fromstring(xml.content)
            body = root.find("body")
            if body is None:
                raise ValueError("缺少全文 body")
            blocks = []

            def collect(node):
                if node.tag in {"p", "title", "table-wrap", "fig"}:
                    content = " ".join("".join(node.itertext()).split())
                    if content:
                        blocks.append(content)
                    return
                for child in node:
                    collect(child)

            collect(body)
            content = "\n\n".join(blocks)
            if len(content) < 1000:
                raise ValueError("全文正文过短")
            license_text = " ".join(
                " ".join(node.itertext()) for node in root.findall(".//license")
            )
            record = {
                "source_id": pmcid + "-fulltext",
                "title": article.get("title", meta.get("title")),
                "content": content,
                "content_type": "fulltext",
                "source": "europe_pmc_open_fulltext",
                "pmid": pmid,
                "pmcid": pmcid,
                "doi": article.get("doi", ""),
                "year": article.get("pubYear", ""),
                "authors": [
                    row.get("fullName", "")
                    for row in article.get("authorList", {}).get("author", [])
                ],
                "topic": meta.get("topic", ""),
                "journal": (article.get("journalInfo") or {}).get("journal", {}).get("title", ""),
                "url": f"https://europepmc.org/articles/{pmcid}",
                "license": license_text,
                "acquisition": {
                    "api": API,
                    "open_access_flag": article["isOpenAccess"],
                    "xml_sha256": hashlib.sha256(xml.content).hexdigest(),
                },
            }
            (output / (pmcid + ".xml")).write_bytes(xml.content)
            (output / (pmcid + ".json")).write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            records.append(
                {
                    "pmid": pmid,
                    "pmcid": pmcid,
                    "characters": len(content),
                    "xml_sha256": record["acquisition"]["xml_sha256"],
                }
            )
        except (requests.RequestException, ValueError, ET.ParseError, KeyError) as exc:
            failures.append({"pmid": pmid, "error_type": type(exc).__name__})
            if len(failures) >= 3 and not records:
                break
    report = {"downloaded": records, "failures": failures, "requested": limit}
    (output / "acquisition_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("单次获取数量必须为 1–20")
    print(json.dumps(fetch(args.database, args.output, args.limit), ensure_ascii=False, indent=2))
