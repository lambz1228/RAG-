"""逐篇复核 v26 的 36 篇 CC BY/CC0 全文候选，产出入库建议清单（不写入索引）。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

XLINK = "{http://www.w3.org/1999/xlink}href"
CC_BY_4 = re.compile(r"creativecommons\.org/licenses/by/4\.0", re.IGNORECASE)
CC0 = re.compile(r"creativecommons\.org/publicdomain/zero/1\.0", re.IGNORECASE)
CC_ANY = re.compile(r"creativecommons\.org/(licenses|publicdomain)", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_title(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def xml_identity(root: ET.Element) -> dict:
    def text(tag: str) -> str:
        node = root.find(f".//{tag}")
        return " ".join("".join(node.itertext()).split()) if node is not None else ""

    ids = {}
    for node in root.iter("article-id"):
        ids.setdefault(node.get("pub-id-type", ""), []).append((node.text or "").strip())
    return {
        "title": text("article-title"),
        "journal": text("journal-title"),
        "year": text("year"),
        "pmid": ids.get("pmid", []),
        "pmcid": ids.get("pmcid", []),
        "doi": ids.get("doi", []),
    }


def xml_licenses(root: ET.Element) -> list[dict]:
    rows = []
    for node in root.iter("license"):
        href = node.get(XLINK) or ""
        statement = " ".join("".join(node.itertext()).split())
        rows.append({
            "license_type": node.get("license-type") or "",
            "href": href,
            "statement": statement,
            "granted_by": node.get("licensee") or "",
        })
    return rows


def classify_license(rows: list[dict]) -> tuple[str, str]:
    hrefs = [row["href"] for row in rows if row["href"]]
    text = " ".join(row["statement"] for row in rows)
    if any(CC_BY_4.search(value) for value in hrefs) or CC_BY_4.search(text):
        return "cc_by_4_0", "许可链接或正文许可条款给出 CC BY 4.0 的版本化 URL"
    if any(CC0.search(value) for value in hrefs) or CC0.search(text):
        return "cc0_1_0", "许可链接或正文许可条款给出 CC0 1.0 的版本化 URL"
    if CC_ANY.search(text) or any(CC_ANY.search(value) for value in hrefs):
        return "cc_variant", "许可为其他 CC 版本或未注明版本"
    return "unclear", "未取得可机器判定的 CC 许可"


def body_chars(root: ET.Element) -> int:
    body = root.find(".//body")
    return len("".join(body.itertext())) if body is not None else 0


def europepmc_batch(pmcids: list[str]) -> dict[str, dict]:
    """一次查询 Europe PMC，独立核对开放获取状态、标识与许可描述。"""
    query = " OR ".join(f"PMCID:{pmcid}" for pmcid in pmcids)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query="
           + urllib.parse.quote(query) + "&format=json&resultType=core&pageSize=100")
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "biokb-audit/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                hits = json.load(response)["resultList"]["result"]
            break
        except Exception:
            if attempt == 2:
                return {}
            time.sleep(2 + attempt)
    index = {}
    for hit in hits:
        pmcid = (hit.get("pmcid") or "").upper()
        if pmcid:
            index[pmcid] = {
                "pmid": hit.get("pmid"), "pmcid": hit.get("pmcid"), "doi": hit.get("doi"),
                "license": hit.get("license"), "is_open_access": hit.get("isOpenAccess"),
                "in_epmc": hit.get("inEPMC"),
                "first_publication_date": hit.get("firstPublicationDate"),
            }
    return index


def database_index(database: Path) -> dict:
    index: dict[str, list[dict]] = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT id,title,filename,metadata FROM documents"):
            metadata = json.loads(row["metadata"])
            pmid = str(metadata.get("pmid") or "")
            if not pmid and str(metadata.get("id") or "").isdigit():
                pmid = str(metadata["id"])
            if not pmid:
                continue
            index.setdefault(pmid, []).append({
                "document_id": row["id"],
                "title": row["title"],
                "content_type": metadata.get("content_type"),
                "doi": metadata.get("doi"),
            })
    return index


def review(staging: Path, database: Path) -> dict:
    inventory = json.loads((staging / "staging_inventory.json").read_text(encoding="utf-8"))
    candidates = [row for row in inventory if row.get("candidate_for_next_batch")]
    index = database_index(database)
    external_records = europepmc_batch([row["pmcid"] for row in candidates])
    rows = []
    for candidate in candidates:
        xml_path = staging / candidate["xml_path"]
        digest_ok = sha256(xml_path) == candidate["xml_sha256"]
        root = ET.parse(xml_path).getroot()
        identity = xml_identity(root)
        licenses = xml_licenses(root)
        license_class, license_note = classify_license(licenses)
        texts = normalize_title(identity["title"])
        existing = index.get(candidate["pmid"], [])
        title_match = (
            any(normalize_title(row["title"]) == texts for row in existing) if existing else None
        )
        ids_consistent = (
            candidate["pmid"] in identity["pmid"]
            and candidate["pmcid"] in identity["pmcid"]
            and (candidate["article_ids"].get("doi") or [""])[0].lower() in
            [value.lower() for value in identity["doi"]]
        )
        fulltext_present = any(row["content_type"] == "fulltext" for row in existing)
        chars = body_chars(root)
        external = external_records.get(candidate["pmcid"].upper(), {})
        external_match = (
            external.get("pmid") == candidate["pmid"]
            and external.get("pmcid") == candidate["pmcid"]
            and (external.get("doi") or "").lower() ==
            (candidate["article_ids"].get("doi") or [""])[0].lower()
        )
        if not digest_ok:
            state, reason = "hold_digest", "XML 摘要与冻结清单不一致"
        elif license_class not in ("cc_by_4_0", "cc0_1_0"):
            state, reason = "exclude_license", license_note
        elif not ids_consistent or title_match is False:
            state, reason = "hold_identity", "XML 标识或题名与 v26 记录不一致"
        elif not external_match or external.get("is_open_access") != "Y":
            state, reason = "hold_identity", "Europe PMC 标识或开放获取状态不一致"
        elif not candidate.get("body_present") or chars < 2000:
            state, reason = "hold_content", f"正文缺失或过短（{chars} 字符）"
        elif fulltext_present:
            state, reason = "exclude_duplicate", "v26 已有该论文全文"
        elif not existing:
            state, reason = "hold_association", "v26 中无对应论文记录"
        else:
            state, reason = "admit_candidate", "许可、身份、正文与关联均通过机器核对"
        rows.append({
            "paper_id": candidate["paper_id"],
            "pmid": candidate["pmid"],
            "pmcid": candidate["pmcid"],
            "doi": (candidate["article_ids"].get("doi") or [""])[0],
            "title": identity["title"],
            "journal": identity["journal"],
            "year": identity["year"],
            "license_class": license_class,
            "license_hrefs": [row["href"] for row in licenses if row["href"]],
            "license_notes": license_note,
            "europe_pmc_license": external.get("license"),
            "europe_pmc_open_access": external.get("is_open_access"),
            "europe_pmc_identity_matches": external_match,
            "xml_sha256_matches": digest_ok,
            "identity_consistent": ids_consistent,
            "title_matches_v26": title_match,
            "body_chars": chars,
            "v26_documents": existing,
            "state": state,
            "reason": reason,
            "human_academic_review": None,
        })
    summary = {}
    for row in rows:
        summary[row["state"]] = summary.get(row["state"], 0) + 1
    return {
        "generated_at_utc": utc_now(),
        "scope": "仅机器核对许可链接、标识、正文与关联；不写入索引，不替代人工终审",
        "staging": str(staging),
        "database": str(database),
        "total": len(rows),
        "summary": summary,
        "human_academic_review": None,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage = args.staging.resolve(strict=True)
    report = review(stage, args.database.resolve(strict=True))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = output.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report["rows"][0]) if report["rows"] else [])
        writer.writeheader()
        writer.writerows(report["rows"])
    print(json.dumps({"total": report["total"], "summary": report["summary"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
