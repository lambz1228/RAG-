"""从 Europe PMC 补充开放许可全文，原始 XML 与待入库 JSON 独立保存。"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
TOPICS = [
    ("mitochondria", '"mitochondrial dynamics" OR "oxidative phosphorylation"'),
    ("mitophagy", "mitophagy AND (PINK1 OR Parkin OR BNIP3)"),
    ("er_stress", '"endoplasmic reticulum stress" AND "unfolded protein response"'),
    ("er_mito_contact", '"mitochondria-associated membranes" OR "ER-mitochondria contact"'),
    ("golgi", '"Golgi apparatus" AND (trafficking OR glycosylation)'),
    ("lysosome", 'lysosome AND (TFEB OR "lysosomal biogenesis" OR mTORC1)'),
    ("autophagy", "autophagy AND (ATG5 OR ATG7 OR LC3 OR ULK1)"),
    ("cytoskeleton", '(microtubule OR "actin filament") AND cytoskeleton'),
    ("mitosis", 'mitosis AND ("spindle assembly checkpoint" OR kinetochore)'),
    ("nucleolus_ribosome", '"ribosome biogenesis" OR nucleolus'),
    ("peroxisome", 'peroxisome AND (PEX OR "peroxisomal biogenesis")'),
    ("cilia", '"primary cilia" OR ciliogenesis'),
]
TOPIC_PATTERNS = {
    "mitochondria": r"mitochondri|oxidative phosphorylation",
    "mitophagy": r"mitophag",
    "er_stress": r"endoplasmic reticulum stress|\ber stress\b|unfolded protein response",
    "er_mito_contact": (
        r"mitochondria.associated membrane|mitochondria.er|er.mitochondria|"
        r"endoplasmic reticulum.mitochondria|\bmercs?\b|\bmam.localized\b"
    ),
    "golgi": r"golgi",
    "lysosome": r"lysosom|\btfeb\b",
    "autophagy": r"autophag|\blc3\b",
    "cytoskeleton": r"cytoskelet|microtubul|actin filament|\bactin\b",
    "mitosis": r"mitos|spindle|kinetochore|aneuploid|chromosome segregation",
    "nucleolus_ribosome": r"nucleol|ribosom",
    "peroxisome": r"peroxisom",
    "cilia": r"cilia|cilium|ciliogen",
}
LICENSE_URL_RE = re.compile(r"https?://(?:www\.)?creativecommons\.org/[^\s\"'<>]+", re.I)
ALLOWED_LICENSE_PATHS = {
    "/licenses/by/4.0",
    "/licenses/by/3.0",
    "/publicdomain/zero/1.0",
}
BAD_PUBLICATION_RE = re.compile(r"retract|erratum|correction|expression of concern", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_text(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(value or "")).split())


def request(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    for attempt in range(2):
        try:
            response = session.get(url, params=params, timeout=(10, 35))
            if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(1)
                continue
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout):
            if attempt:
                raise
            time.sleep(1)
    raise RuntimeError("Europe PMC 请求未完成")


def search(session: requests.Session, query: str, page_size: int = 100) -> tuple[list[dict], str]:
    response = request(
        session,
        API + "/search",
        {"query": query, "format": "json", "resultType": "core", "pageSize": page_size},
    )
    payload = response.json()
    return payload.get("resultList", {}).get("result", []), response.url


def current_corpus(database: Path) -> tuple[list[dict], set[str], str]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        metadata = [json.loads(row[0]) for row in conn.execute("SELECT metadata FROM documents")]
        revision = conn.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()[0]
        conn.rollback()
    current_pmids = {
        str(meta.get("pmid") or meta.get("id"))
        for meta in metadata
        if str(meta.get("pmid") or meta.get("id") or "").isdigit()
    }
    fulltext_pmids = {
        str(meta.get("pmid"))
        for meta in metadata
        if meta.get("content_type") == "fulltext" and meta.get("pmid")
    }
    abstracts = [
        meta
        for meta in metadata
        if meta.get("content_type") == "abstract"
        and str(meta.get("id") or meta.get("pmid") or "").isdigit()
        and str(meta.get("id") or meta.get("pmid")) not in fulltext_pmids
    ]
    return abstracts, current_pmids, str(revision)


def lookup_current(session: requests.Session, pmids: list[str]) -> dict[str, dict]:
    results = {}
    for start in range(0, len(pmids), 40):
        batch = pmids[start : start + 40]
        query = "(" + " OR ".join(f"EXT_ID:{pmid}" for pmid in batch) + ") AND SRC:MED"
        rows, _ = search(session, query)
        for row in rows:
            pmid = str(row.get("pmid") or "")
            if pmid in batch:
                if pmid in results:
                    raise ValueError(f"官方 PMID 返回重复：{pmid}")
                results[pmid] = row
    return results


def publication_types(article: dict) -> list[str]:
    values = (article.get("pubTypeList") or {}).get("pubType", []) or []
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]


def candidate_priority(article: dict) -> tuple[bool, int]:
    """优先原始研究；综述保留作背景资料，均不冒充人工证据评级。"""
    is_review = any("review" in value.lower() for value in publication_types(article))
    return is_review, -int(article["pmid"])


def build_topic_query(topic_terms: str, year: int | None = None) -> str:
    """按指定发表年份检索；未指定时沿用原批次的年份范围。"""
    year_range = f"{year} TO {year}" if year is not None else "2021 TO 2026"
    return (
        f"({topic_terms}) AND OPEN_ACCESS:y AND HAS_ABSTRACT:y AND LANG:eng "
        f"AND SRC:MED AND PUB_YEAR:[{year_range}]"
    )


def accepted_count(items: list[dict], topic: str, origin: str, year: int | None) -> int:
    """续跑时分别计算主题和年份配额，避免其他年份占用名额。"""
    return sum(
        item["topic"] == topic
        and item["origin"] == origin
        and (year is None or str(item.get("year")) == str(year))
        for item in items
    )


def topic_relevant(article: dict, topic: str) -> bool:
    """以题名和摘要作主题初筛，后续仍需逐篇核对科研相关性。"""
    summary = clean_text(
        str(article.get("title") or "") + " " + str(article.get("abstractText") or "")
    )
    return bool(re.search(TOPIC_PATTERNS[topic], summary, re.I))


def valid_candidate(article: dict) -> bool:
    pmid = str(article.get("pmid") or "")
    pmcid = str(article.get("pmcid") or "")
    return (
        pmid.isdigit()
        and bool(re.fullmatch(r"PMC[1-9][0-9]*", pmcid))
        and article.get("isOpenAccess") == "Y"
        and len(clean_text(article.get("abstractText", ""))) >= 500
        and bool(clean_text(article.get("title", "")))
        and not BAD_PUBLICATION_RE.search(clean_text(article.get("title", "")))
        and article.get("isRetracted") != "Y"
        and not any(BAD_PUBLICATION_RE.search(value) for value in publication_types(article))
    )


def parse_fulltext(raw: bytes, article: dict) -> tuple[str, str, str]:
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("全文 XML 超过 10 MB")
    root = ET.fromstring(raw)
    if BAD_PUBLICATION_RE.search(root.attrib.get("article-type", "")):
        raise ValueError("全文为撤稿或更正公告")
    article_ids = {}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "article-id":
            kind = node.attrib.get("pub-id-type", "").lower()
            article_ids.setdefault(kind, set()).add("".join(node.itertext()).strip())
    for kind, expected in (("pmid", str(article["pmid"])), ("pmcid", str(article["pmcid"]))):
        if article_ids.get(kind) != {expected}:
            raise ValueError(f"全文 {kind} 与官方记录不一致或缺失")
    doi = str(article.get("doi") or "").strip().lower()
    if doi and article_ids.get("doi") and {value.lower() for value in article_ids["doi"]} != {doi}:
        raise ValueError("全文 DOI 与官方记录不一致")
    body = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "body"), None)
    if body is None:
        raise ValueError("全文缺少 body")
    blocks = []

    def collect(node: ET.Element) -> None:
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"p", "title", "table-wrap", "fig"}:
            value = " ".join("".join(node.itertext()).split())
            if value:
                blocks.append(value)
            return
        for child in node:
            collect(child)

    collect(body)
    content = "\n\n".join(blocks)
    if not 1000 <= len(content) <= 2_000_000:
        raise ValueError("全文正文长度不符合入库要求")
    licenses = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "license"]
    if len(licenses) != 1:
        raise ValueError("全文许可声明缺失或有多项，需人工核验")
    license_node = licenses[0]
    license_text = " ".join("".join(license_node.itertext()).split())
    license_source = html.unescape(
        " ".join(
            [
                license_text,
                *(str(value) for node in license_node.iter() for value in node.attrib.values()),
            ]
        )
    )
    urls = {value.rstrip(".,;:)]}/") for value in LICENSE_URL_RE.findall(license_source)}
    if not urls:
        raise ValueError("全文许可未给出唯一的 Creative Commons 链接")
    paths = set()
    for url in urls:
        parsed = urlsplit(url)
        if parsed.netloc.lower().removeprefix("www.") != "creativecommons.org" or (
            parsed.query or parsed.fragment
        ):
            raise ValueError("全文许可链接格式不符合要求")
        paths.add(parsed.path.lower().rstrip("/"))
    if len(paths) != 1 or not paths.issubset(ALLOWED_LICENSE_PATHS):
        raise ValueError("全文许可不属于允许的 CC BY 或 CC0 范围")
    license_url = "https://creativecommons.org" + next(iter(paths)) + "/"
    return content, license_text, license_url


def article_from_fulltext(raw: bytes) -> dict:
    """从 Europe PMC 提供的 JATS 原文提取可独立核对的文献元数据。"""
    root = ET.fromstring(raw)

    def local_name(node: ET.Element) -> str:
        return node.tag.rsplit("}", 1)[-1]

    def first(parent: ET.Element, name: str) -> ET.Element | None:
        return next((node for node in parent.iter() if local_name(node) == name), None)

    meta = first(root, "article-meta")
    if meta is None:
        raise ValueError("全文缺少 article-meta")
    ids: dict[str, set[str]] = {}
    for node in meta.iter():
        if local_name(node) == "article-id":
            ids.setdefault(node.attrib.get("pub-id-type", "").lower(), set()).add(
                "".join(node.itertext()).strip()
            )
    for kind in ("pmid", "pmcid"):
        if len(ids.get(kind, set())) != 1:
            raise ValueError(f"全文缺少唯一 {kind}")
    title_node = first(meta, "article-title")
    title = clean_text("".join(title_node.itertext())) if title_node is not None else ""
    abstracts = [
        clean_text("".join(node.itertext()))
        for node in meta.iter()
        if local_name(node) == "abstract"
    ]
    dates = [node for node in meta.iter() if local_name(node) == "pub-date"]
    dates.sort(
        key=lambda node: (
            0
            if node.attrib.get("pub-type") == "epub"
            or node.attrib.get("publication-format") == "electronic"
            else 1
        )
    )
    year = ""
    for date in dates:
        year_node = first(date, "year")
        if year_node is not None and (year_node.text or "").strip().isdigit():
            year = year_node.text.strip()
            break
    journal_node = first(root, "journal-title")
    authors = []
    parents = {child: parent for parent in meta.iter() for child in parent}
    for contrib in meta.iter():
        if local_name(contrib) != "contrib":
            continue
        group = parents.get(contrib)
        while group is not None and local_name(group) != "contrib-group":
            group = parents.get(group)
        role = contrib.attrib.get("contrib-type") or (
            group.attrib.get("content-type") if group is not None else ""
        )
        if role != "author":
            continue
        name_node = first(contrib, "name")
        if name_node is None:
            continue
        given = first(name_node, "given-names")
        surname = first(name_node, "surname")
        full_name = " ".join(
            part.strip()
            for part in (
                (given.text if given is not None else ""),
                (surname.text if surname is not None else ""),
            )
            if part and part.strip()
        )
        if full_name:
            authors.append({"fullName": full_name})
    publication_types = [root.attrib.get("article-type", "")]
    publication_types.extend(
        clean_text("".join(node.itertext()))
        for node in meta.iter()
        if local_name(node) == "subject"
    )
    return {
        "pmid": next(iter(ids["pmid"])),
        "pmcid": next(iter(ids["pmcid"])),
        "doi": next(iter(ids.get("doi", {""}))),
        "pubYear": year,
        "title": title,
        "abstractText": " ".join(abstracts),
        "authorList": {"author": authors},
        "journalInfo": {
            "journal": {
                "title": clean_text("".join(journal_node.itertext()))
                if journal_node is not None
                else ""
            }
        },
        "pubTypeList": {"pubType": [value for value in publication_types if value]},
        "isOpenAccess": "Y",
        "isRetracted": "N",
    }


def make_document(article: dict, topic: str, content: str, license_url: str, xml_hash: str) -> dict:
    pmid = str(article["pmid"])
    pmcid = str(article["pmcid"])
    authors = (article.get("authorList") or {}).get("author", []) or []
    if isinstance(authors, dict):
        authors = [authors]
    return {
        "source_id": pmcid + "-fulltext",
        "title": clean_text(article.get("title", "")),
        "content": content,
        "content_type": "fulltext",
        "source": "europe_pmc_open_fulltext",
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": str(article.get("doi") or ""),
        "year": str(article.get("pubYear") or ""),
        "authors": [row.get("fullName", "") for row in authors if row.get("fullName")],
        "topic": topic,
        "journal": (article.get("journalInfo") or {}).get("journal", {}).get("title", ""),
        "url": f"https://europepmc.org/articles/{pmcid}",
        "license": license_url,
        "publication_types": publication_types(article),
        "xml_sha256": xml_hash,
        "acquisition": {
            "api": API,
            "retrieved_at_utc": utc_now(),
            "open_access_flag": article["isOpenAccess"],
            "xml_sha256": xml_hash,
        },
    }


def save_json_atomic(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def save_bytes_atomic(path: Path, value: bytes) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(value)
    os.replace(temp, path)


def verify_batch(report: dict, database: Path, raw_dir: Path, document_dir: Path) -> None:
    if report["database"] != str(database) or report["database_sha256_before"] != file_sha256(
        database
    ):
        raise ValueError("续跑时源数据库或摘要发生变化")
    pmids = set()
    pmcids = set()
    for item in report["items"]:
        pmid, pmcid = item["pmid"], item["pmcid"]
        if pmid in pmids or pmcid in pmcids:
            raise ValueError("采集清单存在重复 PMID 或 PMCID")
        pmids.add(pmid)
        pmcids.add(pmcid)
        for path, expected in (
            (raw_dir / f"{pmcid}.xml", item["xml_sha256"]),
            (document_dir / f"{pmcid}.json", item["json_sha256"]),
        ):
            if not path.is_file() or file_sha256(path) != expected:
                raise ValueError(f"采集文件缺失或摘要不一致：{path}")
    if {path.stem for path in raw_dir.glob("*.xml")} != pmcids or {
        path.stem for path in document_dir.glob("*.json")
    } != pmcids:
        raise ValueError("批次目录存在未登记文件，需先核对中断位置")


def acquire(
    database: Path,
    output: Path,
    existing_per_topic: int,
    new_per_topic: int,
    selected_topics: set[str] | None = None,
    years: tuple[int, ...] | None = None,
) -> dict:
    database = database.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to((ROOT / "data/research_corpus").resolve()):
        raise ValueError("采集目录必须位于 data/research_corpus 内")
    if (
        output.exists()
        and any(output.iterdir())
        and not (output / "acquisition_report.json").exists()
    ):
        raise ValueError("目标目录已有其他文件，不能作为本批采集目录")
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw_xml"
    document_dir = output / "documents"
    raw_dir.mkdir(exist_ok=True)
    document_dir.mkdir(exist_ok=True)
    report_path = output / "acquisition_report.json"
    report = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else {
            "created_at_utc": utc_now(),
            "database": str(database),
            "database_sha256_before": file_sha256(database),
            "existing_per_topic": existing_per_topic,
            "new_per_topic": new_per_topic,
            "years": list(years or ()),
            "topic_queries": {topic: query for topic, query in TOPICS},
            "items": [],
            "failures": [],
            "searches": [],
        }
    )
    verify_batch(report, database, raw_dir, document_dir)
    if report.get("years", []) != list(years or ()):
        raise ValueError("续跑时年份配额与原批次不一致")
    report["existing_per_topic"] = existing_per_topic
    report["new_per_topic"] = new_per_topic
    selected = {item["pmid"] for item in report["items"]}
    failed_pmids = {
        item["pmid"]
        for item in report["failures"]
        if item.get("pmid") and not item.get("retryable", False)
    }
    abstracts, current_pmids, revision = current_corpus(database)
    if report.get("source_revision", revision) != revision:
        raise ValueError("续跑时源库修订号发生变化")
    report["source_revision"] = revision
    with requests.Session() as session:
        session.headers["User-Agent"] = "BioKB-corpus-expansion/2.2 (+local research project)"
        official = (
            lookup_current(
                session,
                sorted({str(meta.get("id") or meta.get("pmid")) for meta in abstracts}),
            )
            if existing_per_topic
            else {}
        )

        def collect_candidates(
            topic: str,
            origin: str,
            pool: list[dict],
            target: int,
            year: int | None = None,
        ) -> None:
            accepted = accepted_count(report["items"], topic, origin, year)
            attempted = 0
            for article in pool:
                if accepted >= target or attempted >= 12:
                    break
                pmid = str(article["pmid"])
                if pmid in selected or pmid in failed_pmids:
                    continue
                attempted += 1
                pmcid = str(article["pmcid"])
                try:
                    response = request(session, API + f"/{pmcid}/fullTextXML")
                    raw = response.content
                    content, _, license_url = parse_fulltext(raw, article)
                    record = make_document(article, topic, content, license_url, sha256_bytes(raw))
                    json_path = document_dir / f"{pmcid}.json"
                    xml_path = raw_dir / f"{pmcid}.xml"
                    if json_path.exists() or xml_path.exists():
                        raise ValueError("本批次已有同名原文，需人工检查")
                    save_bytes_atomic(xml_path, raw)
                    save_json_atomic(json_path, record)
                    entry = {
                        "pmid": pmid,
                        "pmcid": pmcid,
                        "doi": record["doi"],
                        "title": record["title"],
                        "topic": topic,
                        "year": record["year"],
                        "origin": origin,
                        "source_id": record["source_id"],
                        "license_url": license_url,
                        "publication_types": record["publication_types"],
                        "content_characters": len(content),
                        "xml_sha256": sha256_bytes(raw),
                        "json_sha256": file_sha256(json_path),
                        "article_url": record["url"],
                    }
                    report["items"].append(entry)
                    selected.add(pmid)
                    accepted += 1
                    save_json_atomic(report_path, report)
                    label = f"{topic}/{year}" if year is not None else topic
                    print(f"[{label}] 已收录 {pmcid}（{origin}，{accepted}/{target}）", flush=True)
                except (requests.RequestException, ValueError, ET.ParseError) as exc:
                    report["failures"].append(
                        {
                            "topic": topic,
                            "year": year,
                            "origin": origin,
                            "pmid": pmid,
                            "pmcid": pmcid,
                            "reason": str(exc)
                            if isinstance(exc, ValueError)
                            else "download_failed",
                            "error_type": type(exc).__name__,
                            "retryable": isinstance(exc, requests.RequestException),
                        }
                    )
                    if not isinstance(exc, requests.RequestException):
                        failed_pmids.add(pmid)
                    save_json_atomic(report_path, report)
            label = f"{topic}/{year}" if year is not None else topic
            print(f"[{label}] {origin}：{accepted}/{target}", flush=True)

        for topic, topic_query in TOPICS:
            if selected_topics is not None and topic not in selected_topics:
                continue
            candidates = [
                official.get(str(meta.get("id") or meta.get("pmid")))
                for meta in abstracts
                if meta.get("topic") == topic
            ]
            current_candidates = sorted(
                (
                    row
                    for row in candidates
                    if row
                    and valid_candidate(row)
                    and topic_relevant(row, topic)
                    and (years is None or str(row.get("pubYear")) in {str(year) for year in years})
                ),
                key=lambda row: row["pmid"],
                reverse=True,
            )
            if existing_per_topic:
                collect_candidates(
                    topic, "existing_abstract", current_candidates, existing_per_topic
                )
            for year in years or (None,):
                query = build_topic_query(topic_query, year)
                try:
                    new_candidates, search_url = search(session, query, 80)
                    report["searches"].append(
                        {
                            "topic": topic,
                            "year": year,
                            "url": search_url,
                            "returned": len(new_candidates),
                            "checked_at_utc": utc_now(),
                        }
                    )
                except (requests.RequestException, ValueError) as exc:
                    new_candidates = []
                    report["failures"].append(
                        {
                            "topic": topic,
                            "year": year,
                            "reason": "search_failed",
                            "error_type": type(exc).__name__,
                        }
                    )
                new_candidates = sorted(
                    (
                        row
                        for row in new_candidates
                        if valid_candidate(row)
                        and topic_relevant(row, topic)
                        and str(row["pmid"]) not in current_pmids
                        and (year is None or str(row.get("pubYear")) == str(year))
                    ),
                    key=candidate_priority,
                )
                collect_candidates(topic, "new_paper", new_candidates, new_per_topic, year)
    report["completed_at_utc"] = utc_now()
    report["run_topics"] = sorted(selected_topics or {topic for topic, _ in TOPICS})
    report["database_sha256_after"] = file_sha256(database)
    report["source_unchanged"] = report["database_sha256_before"] == report["database_sha256_after"]
    report["counts"] = {
        "fulltexts": len(report["items"]),
        "existing_abstract_upgrades": sum(
            item["origin"] == "existing_abstract" for item in report["items"]
        ),
        "new_papers": sum(item["origin"] == "new_paper" for item in report["items"]),
        "by_topic": dict(sorted(Counter(item["topic"] for item in report["items"]).items())),
        "by_year": dict(
            sorted(Counter(item.get("year", "unknown") for item in report["items"]).items())
        ),
        "failures": len(report["failures"]),
    }
    save_json_atomic(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-per-topic", type=int, default=1)
    parser.add_argument("--new-per-topic", type=int, default=2)
    parser.add_argument("--topics", nargs="+", choices=[topic for topic, _ in TOPICS])
    parser.add_argument("--years", nargs="+", type=int, help="按年份分别满足每主题新增篇数")
    args = parser.parse_args()
    if not 0 <= args.existing_per_topic <= 5 or not 0 <= args.new_per_topic <= 5:
        parser.error("每主题补全文和新增篇数应为 0–5")
    if args.years and (
        len(set(args.years)) != len(args.years)
        or any(year < 1900 or year > datetime.now().year for year in args.years)
    ):
        parser.error("年份必须唯一，且介于 1900 年和当前年份之间")
    report = acquire(
        args.database,
        args.output,
        args.existing_per_topic,
        args.new_per_topic,
        set(args.topics) if args.topics else None,
        tuple(sorted(args.years)) if args.years else None,
    )
    print(
        json.dumps(
            {"counts": report["counts"], "source_unchanged": report["source_unchanged"]},
            ensure_ascii=False,
        )
    )
    return 0 if report["source_unchanged"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
