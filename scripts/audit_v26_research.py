"""对指定 BioKB 快照做只读文献链审计，并冻结 PubMed 官方响应。"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.papers import build_catalog, identifiers  # noqa: E402

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
RELATION_ALERTS = {
    "ErratumIn", "ErratumFor", "RetractionIn", "RetractionOf",
    "ExpressionOfConcernIn", "ExpressionOfConcernFor", "UpdateIn", "UpdateOf",
    "RepublishedIn", "RepublishedFrom", "CorrectedandRepublishedIn",
    "CorrectedandRepublishedFrom",
}
PUBLICATION_ALERTS = {
    "Retracted Publication", "Retraction of Publication", "Expression of Concern",
    "Published Erratum", "Corrected and Republished Article",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    value = re.sub(r"<[^>]+>", "", value)
    return "".join(char for char in value if char.isalnum())


def article_text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def parse_pubmed(xml_bytes: bytes) -> dict[str, dict]:
    root = ET.fromstring(xml_bytes)
    records = {}
    for article in root.findall("./PubmedArticle"):
        citation = article.find("./MedlineCitation")
        if citation is None:
            continue
        pmid = (citation.findtext("./PMID") or "").strip()
        if not pmid.isdigit() or pmid in records:
            raise ValueError(f"PubMed XML PMID 缺失或重复：{pmid}")
        ids = {}
        for node in article.findall("./PubmedData/ArticleIdList/ArticleId"):
            kind, value = node.get("IdType", ""), (node.text or "").strip()
            if value and kind in {"doi", "pmc", "pubmed"}:
                ids.setdefault(kind, set()).add(value)
        relations = []
        for node in citation.findall("./CommentsCorrectionsList/CommentsCorrections"):
            relations.append({
                "type": node.get("RefType", ""),
                "target_pmid": (node.findtext("./PMID") or "").strip(),
                "citation": article_text(node.find("./RefSource")),
            })
        journal_year = (citation.findtext("./Article/Journal/JournalIssue/PubDate/Year") or "").strip()
        online_year = (citation.findtext("./Article/ArticleDate/Year") or "").strip()
        year = journal_year or online_year
        if not year:
            medline_date = citation.findtext("./Article/Journal/JournalIssue/PubDate/MedlineDate") or ""
            match = re.search(r"\b(?:19|20)\d{2}\b", medline_date)
            year = match.group(0) if match else ""
        records[pmid] = {
            "pmid": pmid,
            "title": article_text(citation.find("./Article/ArticleTitle")),
            "year": year,
            "journal_year": journal_year,
            "online_year": online_year,
            "pmcid": sorted(ids.get("pmc", [])),
            "doi": sorted(ids.get("doi", [])),
            "publication_types": [article_text(node) for node in citation.findall(
                "./Article/PublicationTypeList/PublicationType"
            )],
            "relations": relations,
        }
    return records


def fetch_pubmed(pmids: list[str], output: Path, session: requests.Session) -> tuple[dict, list]:
    raw_dir = output / "pubmed_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records, batches = {}, []
    for start in range(0, len(pmids), 50):
        requested = pmids[start:start + 50]
        path = raw_dir / f"batch_{start // 50 + 1:03d}.xml"
        params = {"db": "pubmed", "id": ",".join(requested), "retmode": "xml"}
        if path.exists():
            reused = True
            body = path.read_bytes()
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            source = requests.Request("GET", EFETCH_URL, params=params).prepare().url
        else:
            reused = False
            for attempt in range(3):
                try:
                    response = session.get(EFETCH_URL, params=params, timeout=(10, 60))
                    response.raise_for_status()
                    body = response.content
                    parsed = parse_pubmed(body)
                    if not parsed:
                        raise ValueError("PubMed 返回空记录")
                    path.write_bytes(body)
                    fetched_at, source = utc_now(), response.url
                    break
                except (requests.RequestException, ET.ParseError, ValueError):
                    if attempt == 2:
                        raise
                    time.sleep(1.0 * (attempt + 1))
        parsed = parse_pubmed(body)
        if set(parsed) - set(requested):
            raise ValueError("PubMed 返回了未请求的 PMID")
        if set(records) & set(parsed):
            raise ValueError("PubMed 分批响应含重复 PMID")
        records.update(parsed)
        batches.append({
            "path": str(path.relative_to(output)), "url": source,
            "retrieved_at_utc": fetched_at, "requested_pmids": requested,
            "reused_frozen_response": reused,
            "returned_pmids": sorted(parsed), "sha256": sha256_file(path),
        })
        time.sleep(0.4)
    return records, batches


def source_inventory(source_root: Path) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    json_paths: dict[str, list[Path]] = {}
    xml_paths: dict[str, list[Path]] = {}
    for path in source_root.rglob("PMC*.json"):
        json_paths.setdefault(path.stem.upper(), []).append(path)
    for path in source_root.rglob("PMC*.xml"):
        xml_paths.setdefault(path.stem.upper(), []).append(path)
    return json_paths, xml_paths


def audit_local(database: Path, source_root: Path) -> dict:
    database = database.resolve(strict=True)
    db_hash_before = sha256_file(database)
    wal = Path(str(database) + "-wal")
    wal_before = {"size": wal.stat().st_size, "sha256": sha256_file(wal)} if wal.exists() else None
    json_paths, xml_paths = source_inventory(source_root)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        documents = [dict(row) for row in connection.execute(
            "SELECT id,content_hash,filename,title,text,metadata FROM documents ORDER BY id"
        )]
        records = [{"id": row["id"], "title": row["title"],
                    "filename": row["filename"], "metadata": json.loads(row["metadata"])}
                   for row in documents]
        chunks = list(connection.execute(
            "SELECT id,document_id,text,start_offset,end_offset FROM chunks ORDER BY document_id,ordinal"
        ))
        profiles = [dict(row) for row in connection.execute(
            "SELECT fingerprint,dimensions,COUNT(*) AS count FROM embeddings GROUP BY fingerprint,dimensions"
        )]
        orphan_vectors = connection.execute(
            "SELECT COUNT(*) FROM embeddings e LEFT JOIN chunks c ON c.id=e.chunk_id WHERE c.id IS NULL"
        ).fetchone()[0]
        missing_vectors = connection.execute(
            "SELECT COUNT(*) FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.id WHERE e.chunk_id IS NULL"
        ).fetchone()[0]
        revision = connection.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[0]
        connection.rollback()
    by_document = {row["id"]: row for row in documents}
    chunk_counts = Counter()
    chunk_errors = []
    for chunk in chunks:
        doc = by_document.get(chunk["document_id"])
        chunk_counts[chunk["document_id"]] += 1
        if (doc is None or chunk["start_offset"] < 0 or
                chunk["end_offset"] > len(doc["text"]) or
                doc["text"][chunk["start_offset"]:chunk["end_offset"]] != chunk["text"]):
            chunk_errors.append(chunk["id"])
    catalog = build_catalog(records)
    source_hashes = {}
    fulltext_checks = []
    for doc in documents:
        meta = json.loads(doc["metadata"])
        if meta.get("content_type") != "fulltext":
            continue
        pmcid = str(meta.get("pmcid") or "").upper()
        candidates = json_paths.get(pmcid, [])
        matching = [path for path in candidates if sha256_file(path) == doc["content_hash"]]
        raw_path = matching[0] if matching else None
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path else {}
        raw_xml = xml_paths.get(pmcid, [])
        expected_xml_hash = raw.get("xml_sha256") or (raw.get("acquisition") or {}).get("xml_sha256")
        matching_xml = [path for path in raw_xml if expected_xml_hash and sha256_file(path) == expected_xml_hash]
        xml_path = matching_xml[0] if matching_xml else None
        if raw_path:
            source_hashes[str(raw_path.relative_to(ROOT))] = sha256_file(raw_path)
        if xml_path:
            source_hashes[str(xml_path.relative_to(ROOT))] = sha256_file(xml_path)
        fields = ("pmid", "pmcid", "doi", "license")
        field_matches = {key: bool(raw) and str(raw.get(key) or "").casefold() ==
                         str(meta.get(key) or "").casefold() for key in fields}
        fulltext_checks.append({
            "document_id": doc["id"], "pmid": str(meta.get("pmid") or ""), "pmcid": pmcid,
            "raw_json": str(raw_path.relative_to(ROOT)) if raw_path else None,
            "raw_json_sha256": source_hashes.get(str(raw_path.relative_to(ROOT))) if raw_path else None,
            "raw_xml": str(xml_path.relative_to(ROOT)) if xml_path else None,
            "raw_xml_sha256": source_hashes.get(str(xml_path.relative_to(ROOT))) if xml_path else None,
            "content_exact": bool(raw) and raw.get("content") == doc["text"],
            "field_matches": field_matches,
            "chunk_count": chunk_counts[doc["id"]],
            "passed": bool(raw_path and xml_path and raw.get("content") == doc["text"]
                           and all(field_matches.values()) and chunk_counts[doc["id"]] > 0),
        })
    entries = []
    for paper in catalog["items"]:
        preferred = by_document[paper["preferred_document_id"]]
        meta = json.loads(preferred["metadata"])
        entries.append({
            "paper_id": paper["id"], "title": paper["title"],
            "identifiers": paper["identifiers"], "year": paper["year"],
            "topics": paper["topics"], "has_fulltext": paper["has_fulltext"],
            "document_ids": paper["document_ids"],
            "preferred_source_id": meta.get("source_id", ""),
            "preferred_text_chars": len(preferred["text"]),
            "license": meta.get("license", "") if paper["has_fulltext"] else "",
            "quality_status": (meta.get("quality") or {}).get("status", ""),
        })
    db_hash_after = sha256_file(database)
    wal_after = {"size": wal.stat().st_size, "sha256": sha256_file(wal)} if wal.exists() else None
    source_after = {name: sha256_file(ROOT / name) for name in source_hashes}
    return {
        "audited_at_utc": utc_now(), "database": str(database),
        "database_sha256_before": db_hash_before, "database_sha256_after": db_hash_after,
        "database_unchanged": db_hash_before == db_hash_after,
        "wal_before": wal_before, "wal_after": wal_after,
        "wal_unchanged": wal_before == wal_after,
        "source_files_sha256": source_hashes,
        "source_files_unchanged": source_hashes == source_after,
        "index_revision": revision, "vector_profiles": profiles,
        "documents": len(documents), "chunks": len(chunks),
        "orphan_vectors": orphan_vectors, "missing_vectors": missing_vectors,
        "chunk_text_errors": chunk_errors,
        "paper_summary": catalog["summary"],
        "fulltext_checks": fulltext_checks,
        "papers": entries,
    }


def audit_official(local: dict, output: Path) -> dict:
    pmids = sorted({paper["identifiers"].get("pmid", "") for paper in local["papers"]} - {""}, key=int)
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-graduation-research/2.6 (PubMed metadata audit)"
        records, batches = fetch_pubmed(pmids, output, session)
        targets = sorted({relation["target_pmid"] for record in records.values()
                          for relation in record["relations"] if relation["target_pmid"].isdigit()}
                         - set(records), key=int)
        target_records, target_batches = fetch_pubmed(targets, output / "relation_targets", session)
        if targets:
            # 目标响应另置目录，避免同名批次覆盖主文献响应。
            for row in target_batches:
                row["path"] = str(Path("relation_targets") / row["path"])
    comparisons, relations = [], []
    for paper in local["papers"]:
        ids = paper["identifiers"]
        pmid = ids.get("pmid", "")
        official = records.get(pmid)
        if official:
            comparisons.append({
                "paper_id": paper["paper_id"], "pmid": pmid,
                "title_matches": normalize_title(paper["title"]) == normalize_title(official["title"]),
                "year_matches": paper["year"] == official["year"],
                "year_matches_any_publication_date": paper["year"] in {
                    official["journal_year"], official["online_year"]
                },
                "official_journal_year": official["journal_year"],
                "official_online_year": official["online_year"],
                "pmcid_matches": not ids.get("pmcid") or ids["pmcid"] in official["pmcid"],
                "doi_matches": not ids.get("doi") or ids["doi"] in [value.lower() for value in official["doi"]],
                "official_pmcids": official["pmcid"], "official_dois": official["doi"],
                "official_title": official["title"], "official_year": official["year"],
                "publication_types": official["publication_types"],
            })
            for relation in official["relations"]:
                target = records.get(relation["target_pmid"]) or target_records.get(relation["target_pmid"])
                doi_match = re.search(r"10\.\d{4,9}/[^\s]+", relation["citation"])
                target_doi = doi_match.group(0).rstrip(".,;)") if doi_match else ""
                relations.append({
                    "source_pmid": pmid, "source_paper_id": paper["paper_id"],
                    **relation,
                    "target_doi_in_citation": target_doi,
                    "target_found": target is not None,
                    "target_title": target["title"] if target else "",
                    "target_publication_types": target["publication_types"] if target else [],
                    "machine_alert": relation["type"] in RELATION_ALERTS,
                    "machine_state": "目标 PMID 已取得官方记录" if target
                    else "仅有 DOI 文字，目标版本待核对" if target_doi
                    else "关联目标未取得官方记录",
                    "requires_human_scope_review": True,
                    "human_academic_decision": None,
                })
    abstract_limit = []
    for paper in local["papers"]:
        if paper["has_fulltext"]:
            continue
        official = records.get(paper["identifiers"].get("pmid", ""), {})
        abstract_limit.append({
            "paper_id": paper["paper_id"], "pmid": paper["identifiers"].get("pmid", ""),
            "title": paper["title"], "abstract_chars": paper["preferred_text_chars"],
            "official_pmcids": official.get("pmcid", []),
            "official_dois": official.get("doi", []),
            "fulltext_status": "仅摘要；PubMed 有 PMCID，尚需逐篇核实可复用许可"
            if official.get("pmcid") else "仅摘要；本次 PubMed 响应未列 PMCID",
            "methods_results_limit": "不能据库内摘要核对全文方法条件、图表与补充材料",
            "acquisition_decision": None,
            "human_academic_review": None,
        })
    return {
        "checked_at_utc": utc_now(), "endpoint": EFETCH_URL,
        "main_batches": batches, "target_batches": target_batches,
        "requested_pmids": pmids, "returned_pmids": sorted(records, key=int),
        "missing_pmids": sorted(set(pmids) - set(records), key=int),
        "records": records, "target_records": target_records,
        "comparisons": comparisons, "relations": relations,
        "abstract_only": abstract_limit,
        "publication_alerts": [pmid for pmid, record in records.items()
                               if set(record["publication_types"]) & PUBLICATION_ALERTS],
        "relation_alert_count": sum(row["machine_alert"] for row in relations),
        "human_academic_review": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=ROOT / "data" / "research_corpus")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    database = args.database.resolve(strict=True)
    if output == database.parent or database.is_relative_to(output):
        parser.error("输出目录不得包含正式数据库")
    output.mkdir(parents=True, exist_ok=True)
    local = audit_local(database, args.sources.resolve(strict=True))
    save_json(output / "local_audit.json", local)
    if not args.offline:
        official = audit_official(local, output)
        save_json(output / "official_audit.json", official)
        save_json(output / "abstract_only_inventory.json", official["abstract_only"])
        save_json(output / "version_relations.json", official["relations"])
    else:
        official = None
    summary = {
        "database_unchanged": local["database_unchanged"],
        "source_files_unchanged": local["source_files_unchanged"],
        "paper_summary": local["paper_summary"],
        "fulltext_chain_passed": sum(row["passed"] for row in local["fulltext_checks"]),
        "fulltext_chain_total": len(local["fulltext_checks"]),
        "chunk_text_errors": len(local["chunk_text_errors"]),
        "pubmed_returned": len(official["returned_pmids"]) if official else None,
        "pubmed_missing": official["missing_pmids"] if official else None,
        "relation_rows": len(official["relations"]) if official else None,
    }
    save_json(output / "audit_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if (local["database_unchanged"] and local["source_files_unchanged"]
                 and not local["chunk_text_errors"] and not local["missing_vectors"]
                 and summary["fulltext_chain_passed"] == summary["fulltext_chain_total"]
                 and (official is None or not official["missing_pmids"])) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
