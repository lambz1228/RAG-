"""用 Crossref DOI 检索和 PMC ID Converter 为未采满的年份主题建立候选清单。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

from scripts.expand_open_corpus import (
    ROOT,
    current_corpus,
    file_sha256,
    request,
    save_bytes_atomic,
    save_json_atomic,
    utc_now,
)

CROSSREF_API = "https://api.crossref.org/works"
ID_CONVERTER_API = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
SEARCH_TERMS = {
    "golgi": "Golgi apparatus trafficking",
    "autophagy": "autophagy ATG",
    "peroxisome": "peroxisome biogenesis",
    "er_mito_contact": "endoplasmic reticulum mitochondria contact",
    "mitosis": "mitosis spindle checkpoint",
    "mitophagy": "mitophagy PINK1",
    "er_stress": "endoplasmic reticulum stress unfolded protein response",
    "cilia": "primary cilia ciliogenesis",
    "lysosome": "lysosome TFEB",
}


def missing_groups(previous: Path) -> list[tuple[str, int]]:
    seeds = json.loads((previous / "seed_candidates.json").read_text(encoding="utf-8"))
    acquisition = json.loads((previous / "acquisition_report.json").read_text(encoding="utf-8"))
    filled = {(item["topic"], int(item["year"])) for item in acquisition["items"]}
    return [
        (group["topic"], group["year"])
        for group in seeds["groups"]
        if (group["topic"], group["year"]) not in filled
    ]


def cached_json(
    session: requests.Session, url: str, params: dict, path: Path
) -> tuple[dict, str]:
    prepared = session.prepare_request(requests.Request("GET", url, params=params))
    if path.is_file():
        raw = path.read_bytes()
    else:
        response = request(session, url, params)
        raw = response.content
        save_bytes_atomic(path, raw)
    return json.loads(raw), prepared.url


def discover(
    database: Path,
    previous: Path,
    output: Path,
    rows: int = 80,
    search_term: str | None = None,
    title_contains: str | None = None,
) -> dict:
    database = database.resolve(strict=True)
    previous = previous.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to((ROOT / "data/research_corpus").resolve()):
        raise ValueError("候选批次必须位于 data/research_corpus 内")
    if not 20 <= rows <= 100:
        raise ValueError("每组 Crossref 候选数应为 20–100")
    if (output / "seed_candidates.json").exists():
        raise FileExistsError("候选清单已冻结，不能覆盖")
    previous_index = json.loads((previous / "index_verification.json").read_text(encoding="utf-8"))
    before = file_sha256(database)
    if not previous_index["passed"] or before != previous_index["target_database_sha256"]:
        raise ValueError("父库与上一批索引核验快照不一致")
    groups = missing_groups(previous)
    if not groups or any(topic not in SEARCH_TERMS for topic, _ in groups):
        raise ValueError("未找到可检索的缺口主题年份")
    if (search_term or title_contains) and len(groups) != 1:
        raise ValueError("定向检索参数仅可用于唯一的缺口组合")
    output.mkdir(parents=True, exist_ok=True)
    snapshot_dir = output / "discovery_snapshots"
    snapshot_dir.mkdir(exist_ok=True)
    _, current_pmids, revision = current_corpus(database)
    seed_groups = []
    details = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-research-corpus/2.4 (academic project)"
        for topic, year in groups:
            stem = f"{year}-{topic}"
            term = search_term or SEARCH_TERMS[topic]
            crossref_params = {
                "query.title": term,
                "filter": (
                    f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,"
                    "type:journal-article"
                ),
                "rows": rows,
                "select": "DOI,title,published",
            }
            crossref_path = snapshot_dir / f"{stem}-crossref.json"
            crossref, search_url = cached_json(
                session, CROSSREF_API, crossref_params, crossref_path
            )
            works = crossref.get("message", {}).get("items", [])
            dois = []
            doi_titles = {}
            for work in works:
                doi = str(work.get("DOI") or "").strip()
                title = (work.get("title") or [""])[0]
                if title_contains and title_contains.casefold() not in title.casefold():
                    continue
                if not doi.lower().startswith("10.") or "," in doi or doi.lower() in doi_titles:
                    continue
                dois.append(doi)
                doi_titles[doi.lower()] = title
            converted = {}
            conversion_files = []
            for part, start in enumerate(range(0, len(dois), 30), 1):
                params = {
                    "ids": ",".join(dois[start : start + 30]),
                    "format": "json",
                    "tool": "BioKB",
                }
                path = snapshot_dir / f"{stem}-idconv-{part}.json"
                result, url = cached_json(session, ID_CONVERTER_API, params, path)
                if result.get("status") != "ok":
                    raise ValueError(f"PMC ID Converter 返回异常：{stem}/{part}")
                conversion_files.append(
                    {"path": path.name, "url": url, "sha256": file_sha256(path)}
                )
                for record in result.get("records", []):
                    doi = str(record.get("doi") or "").lower()
                    if doi not in doi_titles or not record.get("pmcid"):
                        continue
                    pmid = str(record.get("pmid") or "")
                    if pmid.isdigit() and pmid not in current_pmids:
                        converted.setdefault(
                            doi,
                            {
                                "pmcid": record["pmcid"],
                                "pmid": pmid,
                                "doi": doi,
                                "crossref_title": doi_titles[doi],
                            },
                        )
                time.sleep(0.35)
            candidates = []
            seen = set()
            for doi in dois:
                record = converted.get(doi.lower())
                if record and record["pmcid"] not in seen:
                    candidates.append(record)
                    seen.add(record["pmcid"])
            if candidates:
                seed_groups.append(
                    {
                        "topic": topic,
                        "year": year,
                        "q": f"Crossref query.title={term}; published={year}"
                        + (f"; title contains {title_contains}" if title_contains else ""),
                        "source_url": search_url,
                        "ids": [record["pmcid"] for record in candidates],
                    }
                )
            details.append(
                {
                    "topic": topic,
                    "year": year,
                    "search_url": search_url,
                    "crossref_snapshot": crossref_path.name,
                    "crossref_sha256": file_sha256(crossref_path),
                    "crossref_returned": len(works),
                    "title_contains": title_contains or "",
                    "doi_count": len(dois),
                    "id_converter_snapshots": conversion_files,
                    "eligible_pmc_candidates": len(candidates),
                    "candidates": candidates,
                }
            )
            print(f"[{year}/{topic}] 可核对 PMC 候选：{len(candidates)}", flush=True)
    after = file_sha256(database)
    if before != after:
        raise ValueError("候选发现期间父库文件发生变化")
    seed_path = output / "seed_candidates.json"
    seed = {
        "created_at_utc": utc_now(),
        "discovery_source": "Crossref REST DOI search and PMC ID Converter"
        + (f"; title contains {title_contains}" if title_contains else ""),
        "max_attempts_per_group": 30,
        "groups": seed_groups,
    }
    save_json_atomic(seed_path, seed)
    report = {
        "created_at_utc": utc_now(),
        "parent_database_sha256": after,
        "parent_revision": revision,
        "previous_acquisition_sha256": file_sha256(previous / "acquisition_report.json"),
        "previous_index_sha256": file_sha256(previous / "index_verification.json"),
        "crossref_rows_per_group": rows,
        "target_groups": len(groups),
        "groups_with_candidates": len(seed_groups),
        "seed_manifest_sha256": file_sha256(seed_path),
        "details": details,
    }
    save_json_atomic(output / "discovery_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--previous-batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=80)
    parser.add_argument("--search-term")
    parser.add_argument("--title-contains")
    args = parser.parse_args()
    report = discover(
        args.database,
        args.previous_batch,
        args.output,
        args.rows,
        args.search_term,
        args.title_contains,
    )
    print(
        json.dumps(
            {
                "target_groups": report["target_groups"],
                "groups_with_candidates": report["groups_with_candidates"],
                "seed_manifest_sha256": report["seed_manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
