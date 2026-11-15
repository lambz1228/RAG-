"""从原始资料派生论文目录与研究范围，不改写文献和既有实验编号。"""

from __future__ import annotations

import hashlib
import json
import re

from .errors import KnowledgeError, NotFoundError


def identifiers(metadata: dict) -> dict[str, str]:
    result = {}
    pmid = str(metadata.get("pmid") or "").strip()
    if not pmid and metadata.get("source") == "europe_pmc":
        pmid = str(metadata.get("id") or "").strip()
    if re.fullmatch(r"[1-9][0-9]*", pmid):
        result["pmid"] = pmid
    pmcid = str(metadata.get("pmcid") or "").strip().upper()
    if re.fullmatch(r"PMC[1-9][0-9]*", pmcid):
        result["pmcid"] = pmcid
    doi = str(metadata.get("doi") or "").strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        result["doi"] = doi
    return result


def build_catalog(records: list[dict]) -> dict:
    """仅按一致的正式标识关联合并；相同标题及冲突标识不作为合并依据。"""
    groups: list[dict] = []
    eligible = [r for r in records if r["metadata"].get("content_type") in {"abstract", "fulltext"}]
    for record in sorted(eligible, key=lambda item: item["id"]):
        ids = identifiers(record["metadata"])
        group = {"records": [record], "identifiers": ids, "identity_conflict": False}
        for previous in list(groups):
            shared = set(ids) & set(previous["identifiers"])
            if not any(ids[key] == previous["identifiers"][key] for key in shared):
                continue
            if any(ids[key] != previous["identifiers"][key] for key in shared):
                previous["identity_conflict"] = group["identity_conflict"] = True
                continue
            group["records"].extend(previous["records"])
            ids = {**previous["identifiers"], **ids}
            group["identifiers"] = ids
            group["identity_conflict"] |= previous["identity_conflict"]
            groups.remove(previous)
        groups.append(group)
    papers = []
    for group in groups:
        versions = sorted(
            group["records"],
            key=lambda r: (r["metadata"].get("content_type") != "fulltext", r["id"]),
        )
        preferred = versions[0]
        ids = group["identifiers"]
        identity = next(
            (key + ":" + ids[key] for key in ("pmid", "pmcid", "doi") if key in ids),
            "document:" + preferred["id"],
        )
        # 冲突项保持独立，不能因共享的一个标识生成同一个目录键。
        if group["identity_conflict"]:
            identity += ":" + min(r["id"] for r in versions)
        metadata = preferred["metadata"]
        papers.append(
            {
                "id": "paper_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                "title": preferred["title"],
                "identifiers": ids,
                "authors": metadata.get("authors") or [],
                "journal": metadata.get("journal") or "",
                "year": str(metadata.get("year") or ""),
                "topics": sorted(
                    {str(r["metadata"]["topic"]) for r in versions if r["metadata"].get("topic")}
                ),
                "keywords": metadata.get("keywords") or [],
                "has_fulltext": any(
                    r["metadata"].get("content_type") == "fulltext" for r in versions
                ),
                "preferred_document_id": preferred["id"],
                "document_ids": [r["id"] for r in versions],
                "records": [
                    {
                        "id": r["id"],
                        "content_type": r["metadata"]["content_type"],
                        "filename": r["filename"],
                        "url": r["metadata"].get("url", ""),
                    }
                    for r in versions
                ],
                "identity_conflict": group["identity_conflict"],
            }
        )
    papers.sort(key=lambda p: (p["year"], p["title"], p["id"]), reverse=True)
    return {
        "items": papers,
        "summary": {
            "papers": len(papers),
            "fulltext_papers": sum(p["has_fulltext"] for p in papers),
            "abstract_only_papers": sum(not p["has_fulltext"] for p in papers),
            "records": len(records),
            "paper_records": len(eligible),
            "reference_stubs": sum(
                r["metadata"].get("content_type") == "reference_stub" for r in records
            ),
            "identity_conflicts": sum(p["identity_conflict"] for p in papers),
        },
    }


def list_papers(
    repository, query="", topic="", year="", fulltext=False, limit=20, offset=0
) -> dict:
    catalog = build_catalog(repository.document_catalog())
    query = query.strip().casefold()
    items = [
        p
        for p in catalog["items"]
        if (
            not query
            or query
            in json.dumps(
                {k: p[k] for k in ("title", "identifiers", "authors", "journal", "keywords")},
                ensure_ascii=False,
            ).casefold()
        )
        and (not topic or topic in p["topics"])
        and (not year or year == p["year"])
        and (not fulltext or p["has_fulltext"])
    ]
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "summary": catalog["summary"],
        "topics": sorted({t for p in catalog["items"] for t in p["topics"]}),
        "years": sorted({p["year"] for p in catalog["items"] if p["year"]}, reverse=True),
    }


def resolve_scope(repository, paper_ids: list[str] | None, catalog: dict | None = None) -> dict:
    if paper_ids is None:
        paper_ids = []
    if (
        not isinstance(paper_ids, list)
        or len(paper_ids) > 20
        or any(
            not isinstance(i, str) or not re.fullmatch(r"paper_[a-f0-9]{24}", i) for i in paper_ids
        )
    ):
        raise KnowledgeError("研究范围需为最多 20 个有效论文编号")
    chosen = list(dict.fromkeys(paper_ids))
    if not chosen:
        return {"kind": "library", "papers": [], "document_ids": []}
    catalog = catalog or build_catalog(repository.document_catalog())
    by_id = {p["id"]: p for p in catalog["items"]}
    if any(key not in by_id for key in chosen):
        raise NotFoundError("选定论文已不存在或标识已变更，请重新选择研究范围")
    selected = [by_id[key] for key in chosen]
    return {
        "kind": "papers",
        "papers": [
            {"id": p["id"], "title": p["title"], "identifiers": p["identifiers"]} for p in selected
        ],
        "document_ids": sorted({doc for p in selected for doc in p["document_ids"]}),
    }
