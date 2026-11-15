"""逐篇登记 v26 仅摘要论文在 Europe PMC 的全文可用状态。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    official = json.loads(args.official.read_text(encoding="utf-8"))
    papers = official["abstract_only"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "europepmc_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    by_pmid = {}
    batches = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-graduation-research/2.6 (access audit)"
        for start in range(0, len(papers), 20):
            subset = papers[start:start + 20]
            pmids = [row["pmid"] for row in subset]
            path = raw_dir / f"abstracts_{start // 20 + 1:03d}.json"
            query = " OR ".join("EXT_ID:" + value for value in pmids)
            params = {"query": query, "format": "json", "resultType": "core", "pageSize": 100}
            if path.exists():
                body, retrieved_at, url = path.read_bytes(), None, "已存在的冻结响应"
            else:
                for attempt in range(3):
                    try:
                        response = session.get(SEARCH_URL, params=params, timeout=(10, 60))
                        response.raise_for_status()
                        body = response.content
                        payload = json.loads(body)
                        if "resultList" not in payload:
                            raise ValueError("Europe PMC 响应不含结果列表")
                        path.write_bytes(body)
                        retrieved_at = datetime.now(timezone.utc).isoformat()
                        url = response.url
                        break
                    except (requests.RequestException, ValueError):
                        if attempt == 2:
                            raise
                        time.sleep(attempt + 1)
            payload = json.loads(body)
            results = payload.get("resultList", {}).get("result", [])
            for row in results:
                pmid = str(row.get("pmid") or "")
                if pmid in pmids and row.get("source") == "MED":
                    if pmid in by_pmid:
                        raise ValueError(f"Europe PMC 重复 PMID：{pmid}")
                    by_pmid[pmid] = row
            batches.append({
                "path": str(path.relative_to(output)), "url": url,
                "retrieved_at_utc": retrieved_at, "requested_pmids": pmids,
                "returned_pmids": [str(row.get("pmid") or "") for row in results
                                    if str(row.get("pmid") or "") in pmids and row.get("source") == "MED"],
                "sha256": hashlib.sha256(body).hexdigest(),
            })
            time.sleep(0.4)
    inventory = []
    for paper in papers:
        row = by_pmid.get(paper["pmid"])
        inventory.append({
            **paper,
            "europepmc_found": row is not None,
            "europepmc_pmcid": row.get("pmcid", "") if row else "",
            "europepmc_open_access": row.get("isOpenAccess", "") if row else "",
            "europepmc_fulltext_indexed": row.get("inEPMC", "") if row else "",
            "europepmc_pdf": row.get("hasPDF", "") if row else "",
            "fulltext_acquisition_state": (
                "待逐篇核实许可与原始 XML" if row and row.get("isOpenAccess") == "Y"
                else "接口未找到，待复查" if not row
                else "当前未确认可复用开放全文"
            ),
            "license_verified": False,
            "human_academic_review": None,
        })
    summary = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "v26 中 132 篇仅摘要论文的开放全文可用性登记；OA 标志不等于许可核验",
        "total": len(inventory),
        "found": sum(row["europepmc_found"] for row in inventory),
        "open_access_flag": sum(row["europepmc_open_access"] == "Y" for row in inventory),
        "fulltext_indexed_flag": sum(row["europepmc_fulltext_indexed"] == "Y" for row in inventory),
        "missing": [row["pmid"] for row in inventory if not row["europepmc_found"]],
        "batches": batches,
    }
    (output / "abstract_access_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "abstract_access_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in ("total", "found", "open_access_flag", "fulltext_indexed_flag", "missing")}, ensure_ascii=False))
    return 0 if summary["total"] == 132 and summary["found"] == 132 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
