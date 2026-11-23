"""通过 NCBI E-utilities 直接检索与导入 PubMed 记录。"""

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from .errors import KnowledgeError, ProviderError
from .literature import TOPICS, validate_search
from .literature_records import archive_record, ingest_record

API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_request_lock = threading.Lock()
_last_start = 0.0


def parse_records(raw: bytes) -> dict:
    try:
        root = ET.fromstring(raw)
        if root.tag != "PubmedArticleSet":
            raise ValueError
    except (ET.ParseError, ValueError) as exc:
        raise ProviderError("PubMed 返回的文献 XML 格式不正确") from exc

    def content(node):
        return " ".join("".join(node.itertext()).split()) if node is not None else ""

    result = {}
    for entry in root.findall("PubmedArticle"):
        citation = entry.find("MedlineCitation")
        if citation is None:
            continue
        article = citation.find("Article")
        pmid = citation.findtext("PMID", "")
        if article is None or not re.fullmatch(r"[0-9]{1,10}", pmid):
            continue
        title = content(article.find("ArticleTitle"))
        if not title:
            continue
        ids = {
            node.get("IdType"): content(node)
            for node in entry.findall("PubmedData/ArticleIdList/ArticleId")
        }
        authors = [
            author.findtext("CollectiveName")
            or " ".join(filter(None, (author.findtext("ForeName"), author.findtext("LastName"))))
            for author in article.findall("AuthorList/Author")
        ]
        date = article.find("Journal/JournalIssue/PubDate")
        year = (
            re.search(r"\b\d{4}\b", date.findtext("Year") or content(date))
            if date is not None
            else None
        )
        abstract = []
        for node in article.findall("Abstract/AbstractText"):
            value = content(node)
            if value:
                label = node.get("Label")
                abstract.append(f"{label}: {value}" if label else value)
        result[pmid] = {
            "pmid": pmid,
            "pmcid": ids.get("pmc", ""),
            "doi": ids.get("doi", ""),
            "title": title,
            "authors": ", ".join(filter(None, authors)),
            "journal": content(article.find("Journal/Title")),
            "year": year[0] if year else "",
            "abstract": "\n\n".join(abstract),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return result


class PubMedService:
    def __init__(self, ingestion, settings):
        self.ingestion, self.settings = ingestion, settings

    def _get(self, operation: str, params: dict) -> bytes:
        global _last_start
        params = {**params, "db": "pubmed", "tool": "BioKB"}
        if email := os.environ.get("BIOKB_PUBMED_EMAIL", "").strip():
            params["email"] = email
        # NCBI 无密钥调用每个 IP 最多每秒三次。本进程留出少量间隔余量。
        with _request_lock:
            time.sleep(max(0.0, 0.36 - (time.monotonic() - _last_start)))
            _last_start = time.monotonic()
        try:
            with requests.get(
                API + operation, params=params, timeout=(10, 25), stream=True, allow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise ProviderError(f"PubMed 文献服务返回 HTTP {response.status_code}")
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 5 * 1024 * 1024:
                        raise ProviderError("PubMed 响应过大，请缩小检索范围")
                return bytes(raw)
        except requests.RequestException as exc:
            raise ProviderError("PubMed 文献服务连接失败") from exc

    def _fetch(self, ids: list[str]) -> bytes:
        return self._get("efetch.fcgi", {"id": ",".join(ids), "retmode": "xml"})

    def search(self, topic, keywords, year_from, year_to, page, page_size, open_access=False):
        words = validate_search(topic, keywords, year_from, year_to, page, page_size, open_access)
        if open_access:
            raise KnowledgeError("PubMed 直连不提供开放许可筛选，请使用 Europe PMC")
        topic_query = re.sub(
            r'TITLE_ABS:("[^"]+"|[\w-]+)', r"\1[Title/Abstract]", TOPICS[topic]["query"]
        )
        clauses = [f"({topic_query})", f"{year_from}:{year_to}[dp]"]
        clauses.extend(f'"{word}"[Title/Abstract]' for word in words)
        query = " AND ".join(clauses)
        raw = self._get(
            "esearch.fcgi",
            {
                "term": query,
                "retmode": "json",
                "retmax": page_size,
                "retstart": (page - 1) * page_size,
                "sort": "pub date",
            },
        )
        try:
            result = json.loads(raw)["esearchresult"]
            ids = result["idlist"]
            total = int(result["count"])
            if not isinstance(ids, list) or any(not re.fullmatch(r"[0-9]{1,10}", x) for x in ids):
                raise ValueError
        except (KeyError, ValueError, TypeError) as exc:
            raise ProviderError("PubMed 返回的检索结果格式不正确") from exc
        records = parse_records(self._fetch(ids)) if ids else {}
        return {
            "items": [records[x] for x in ids if x in records],
            "total": total,
            "page": page,
            "page_size": page_size,
            "query": query,
            "source": "PubMed",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def import_article(self, pmid, topic, progress=None):
        if (
            not isinstance(pmid, str)
            or not re.fullmatch(r"[0-9]{1,10}", pmid)
            or topic not in TOPICS
        ):
            raise KnowledgeError("论文编号或主题不正确")
        raw = self._fetch([pmid])
        record = parse_records(raw).get(pmid)
        if not record:
            raise KnowledgeError("PubMed 未找到所选 PMID")
        digest = archive_record(self.settings.data_dir, "pubmed", f"PMID{pmid}.xml", raw)
        record.update(source="pubmed", source_id=f"PMID{pmid}-pubmed", topic=topic)
        result = ingest_record(self.ingestion, record, {"source_metadata_sha256": digest}, progress)
        return {**result, "source_metadata_sha256": digest, "pmid": pmid}
