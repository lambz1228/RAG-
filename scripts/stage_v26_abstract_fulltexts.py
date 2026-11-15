"""对 v26 仅摘要中的 OA 候选逐篇保存原始 XML 与许可核对状态。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def inspect_xml(body: bytes, pmcid: str, pmid: str) -> dict:
    root = ET.fromstring(body)
    ids = {}
    for node in root.findall(".//front/article-meta/article-id"):
        kind, value = node.get("pub-id-type", ""), (node.text or "").strip()
        if value:
            ids.setdefault(kind, []).append(value)
    license_nodes = root.findall(".//front/article-meta/permissions/license")
    licenses = []
    for node in license_nodes:
        licenses.append({
            "license_type": node.get("license-type", ""),
            "href": node.get("{http://www.w3.org/1999/xlink}href", ""),
            "text": " ".join("".join(node.itertext()).split())[:1000],
        })
    has_body = root.find(".//body") is not None
    identity_ok = (pmcid in ids.get("pmcid", []) or
                   pmcid.removeprefix("PMC") in ids.get("pmc", [])) and (
        not ids.get("pmid") or pmid in ids["pmid"]
    )
    license_string = " ".join(
        (row["href"] + " " + row["text"]).lower() for row in licenses
    )
    cc_by = bool(re.search(r"creativecommons\.org/licenses/by/(?:4\.0|3\.0|2\.0)(?:/|\b)",
                           license_string))
    cc0 = "creativecommons.org/publicdomain/zero/" in license_string
    return {
        "article_ids": ids, "license_statements": licenses,
        "identity_matches": identity_ok, "body_present": has_body,
        "license_class": "CC BY 或 CC0，仍需核对具体条款" if cc_by or cc0
        else "存在许可但不是自动允许的 CC BY/CC0" if licenses
        else "原始 XML 未见明确许可",
        "cc_by_or_cc0_uri_detected": cc_by or cc0,
        "candidate_for_next_batch": identity_ok and has_body and (cc_by or cc0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.access_inventory.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "xml"
    raw_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-graduation-research/2.6 (OA XML staging)"
        for index, item in enumerate(inventory, 1):
            pmcid = item["europepmc_pmcid"]
            base = {
                "paper_id": item["paper_id"], "pmid": item["pmid"],
                "pmcid": pmcid, "open_access_flag": item["europepmc_open_access"],
                "human_academic_review": None,
            }
            if item["europepmc_open_access"] != "Y" or not re.fullmatch(r"PMC[0-9]+", pmcid):
                results.append({**base, "state": "未确认可自动获取和复用的 OA 原文"})
                continue
            path = raw_dir / f"{pmcid}.xml"
            if path.exists():
                body, retrieved_at, url = path.read_bytes(), None, "已存在的冻结 XML"
            else:
                url = f"{BASE_URL}/{pmcid}/fullTextXML"
                for attempt in range(3):
                    try:
                        response = session.get(url, timeout=(10, 60))
                        response.raise_for_status()
                        body = response.content
                        inspect_xml(body, pmcid, item["pmid"])
                        path.write_bytes(body)
                        retrieved_at = datetime.now(timezone.utc).isoformat()
                        break
                    except (requests.RequestException, ET.ParseError, ValueError) as exc:
                        if attempt == 2:
                            results.append({**base, "state": "XML 获取或解析失败",
                                            "error_type": type(exc).__name__})
                            body = None
                        else:
                            time.sleep(attempt + 1)
                if body is None:
                    continue
            try:
                details = inspect_xml(body, pmcid, item["pmid"])
            except ET.ParseError:
                results.append({**base, "state": "冻结 XML 解析失败"})
                continue
            results.append({
                **base, **details,
                "state": "机器候选，下一版入库前仍需核实许可条款与版本" if details["candidate_for_next_batch"]
                else "许可或身份需人工复查，禁止自动入库",
                "xml_path": str(path.relative_to(output)),
                "xml_sha256": hashlib.sha256(body).hexdigest(),
                "retrieved_at_utc": retrieved_at, "url": url,
            })
            if index % 10 == 0:
                print(f"已检查 {index}/{len(inventory)} 篇", flush=True)
            save(output / "staging_inventory.json", results)
            time.sleep(0.4)
    summary = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "total": len(inventory), "oa_flag": sum(row["open_access_flag"] == "Y" for row in results),
        "xml_saved": sum(bool(row.get("xml_path")) for row in results),
        "cc_by_or_cc0_uri_candidate": sum(row.get("candidate_for_next_batch", False) for row in results),
        "download_failures": [row["pmid"] for row in results if "失败" in row["state"]],
        "note": "此处只冻结 XML、标识与许可机器检查；未写入 v26 索引，未进行学术审读。",
    }
    save(output / "staging_inventory.json", results)
    save(output / "staging_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if len(results) == len(inventory) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
