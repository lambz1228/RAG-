"""解析 v26 版本关系中只有 DOI 的目标，补齐 Crossref 与 Europe PMC 身份链。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "biokb-audit/1.0 (mailto:audit@example.org)"
CROSSREF = "https://api.crossref.org/works/"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url: str, attempts: int = 4) -> dict:
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except (urllib.error.HTTPError, TimeoutError, urllib.error.URLError) as error:
            last = error
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"请求失败：{url}：{last}")


def crossref(doi: str) -> dict:
    message = fetch_json(CROSSREF + urllib.parse.quote(doi))["message"]
    return {
        "doi": message.get("DOI"),
        "title": (message.get("title") or [""])[0],
        "type": message.get("type"),
        "version": (message.get("version") or {}).get("version"),
        "posted": message.get("posted", {}).get("date-parts", [None])[0],
        "published": message.get("published", {}).get("date-parts", [None])[0],
        "publisher": message.get("publisher"),
        "authors": [
            f"{author.get('family', '')} {author.get('given', '')}".strip()
            for author in message.get("author", [])
        ],
        "licenses": [row.get("URL") for row in message.get("license", [])],
        "relations": message.get("relation", {}),
        "url": "https://api.crossref.org/works/" + urllib.parse.quote(doi),
    }


def europepmc(doi: str) -> dict:
    query = urllib.parse.quote(f'DOI:"{doi}"')
    payload = fetch_json(f"{EPMC}?query={query}&format=json&resultType=core&pageSize=5")
    hits = payload.get("resultList", {}).get("result", [])
    return {
        "query": f'DOI:"{doi}"',
        "records": [
            {"id": hit.get("id"), "source": hit.get("source"), "pmid": hit.get("pmid"),
             "pmcid": hit.get("pmcid"), "pub_type": hit.get("pubType"),
             "title": hit.get("title"), "first_publication_date": hit.get("firstPublicationDate")}
            for hit in hits
        ],
    }


def relation_values(record: dict, key: str) -> list[str]:
    return [row["id"] for row in record["relations"].get(key, []) if row.get("id")]


def resolve(relations: list[dict], pmid: str, closure_doi: str | None) -> dict:
    targets = [
        row for row in relations
        if row.get("source_pmid") == pmid and row.get("target_doi_in_citation")
    ]
    if not targets:
        raise ValueError(f"未找到 PMID {pmid} 的 DOI 目标")
    records = []
    for row in targets:
        doi = row["target_doi_in_citation"]
        record = crossref(doi)
        record["europe_pmc"] = europepmc(doi)
        record["matched_pmid_pmc"] = [
            hit for hit in record["europe_pmc"]["records"]
            if hit.get("pmid") == pmid or hit.get("pmcid")
        ]
        record["retrieved_at_utc"] = utc_now()
        records.append(record)
    closure = None
    if closure_doi:
        closure = crossref(closure_doi)
        closure["europe_pmc"] = europepmc(closure_doi)
        closure["retrieved_at_utc"] = utc_now()
        closure["same_work_links"] = relation_values(closure, "is-same-as")
    print(f"已解析 {len(records)} 个 DOI 目标")
    return {
        "generated_at_utc": utc_now(),
        "source_pmid": pmid,
        "method": "Crossref works API + Europe PMC REST search，按 DOI 精确查询",
        "records": records,
        "chain_closure": closure,
        "human_academic_decision": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relations", type=Path, required=True)
    parser.add_argument("--pmid", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--closure-doi", default=None)
    args = parser.parse_args()
    relations = json.loads(args.relations.read_text(encoding="utf-8"))
    result = resolve(relations, args.pmid, args.closure_doi)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in result["records"]:
        print(json.dumps({
            "doi": record["doi"], "type": record["type"], "version": record["version"],
            "posted": record["posted"], "title": record["title"],
            "authors": record["authors"], "licenses": record["licenses"],
            "is_version_of": relation_values(record, "is-version-of"),
            "is_preprint_of": relation_values(record, "is-preprint-of"),
            "epmc_records": record["europe_pmc"]["records"],
        }, ensure_ascii=False, indent=2))
    if result["chain_closure"]:
        closure = result["chain_closure"]
        print(json.dumps({
            "closure_doi": closure["doi"], "type": closure["type"],
            "published": closure["published"], "title": closure["title"],
            "is_same_as": closure["same_work_links"],
            "is_version_of": relation_values(closure, "is-version-of"),
            "has_preprint": relation_values(closure, "has-preprint"),
            "epmc_records": closure["europe_pmc"]["records"],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
