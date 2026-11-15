"""细胞生物学文献发现与入库，仅访问 Europe PMC 的固定官方 API。"""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from .errors import KnowledgeError, ProviderError

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
TOPICS = {
    "mitochondria": {
        "label": "线粒体与能量代谢",
        "query": "(TITLE_ABS:mitochondria OR TITLE_ABS:mitochondrial) AND (TITLE_ABS:metabolism OR TITLE_ABS:bioenergetics)",
    },
    "autophagy": {
        "label": "自噬与溶酶体",
        "query": "TITLE_ABS:autophagy AND (TITLE_ABS:lysosome OR TITLE_ABS:lysosomal)",
    },
    "signaling": {
        "label": "细胞信号转导",
        "query": "(TITLE_ABS:EGFR OR TITLE_ABS:MAPK OR TITLE_ABS:mTOR) AND (TITLE_ABS:signaling OR TITLE_ABS:signalling)",
    },
    "endoplasmic_reticulum": {
        "label": "内质网与蛋白质稳态",
        "query": "TITLE_ABS:endoplasmic AND TITLE_ABS:reticulum AND (TITLE_ABS:proteostasis OR TITLE_ABS:homeostasis)",
    },
    "cell_cycle": {
        "label": "细胞周期与凋亡",
        "query": 'TITLE_ABS:"cell cycle" AND TITLE_ABS:apoptosis',
    },
    "peroxisome": {
        "label": "过氧化物酶体",
        "query": "(TITLE_ABS:peroxisome OR TITLE_ABS:peroxisomal)",
    },
}


def plain(value) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))).split())


class LiteratureService:
    def __init__(self, ingestion, settings, session=None):
        self.ingestion, self.settings = ingestion, settings
        self.session = session or requests.Session()

    def _get(self, path: str, params=None, limit=5 * 1024 * 1024) -> bytes:
        try:
            with self.session.get(
                API + path,
                params=params,
                timeout=(10, 40),
                stream=True,
                headers={"User-Agent": "BioKB-graduation/3.0"},
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ProviderError("文献服务暂不可用，请稍后重试")
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > limit:
                        raise KnowledgeError("文献响应过大，请改用手动导入")
                return bytes(raw)
        except requests.RequestException as exc:
            raise ProviderError("Europe PMC 连接失败，请稍后重试或手动导入") from exc

    def search(
        self,
        topic="mitochondria",
        keywords="",
        year_from=2021,
        year_to=2026,
        page=1,
        page_size=10,
        open_access=True,
    ):
        if topic not in TOPICS:
            raise KnowledgeError("请选择支持的细胞生物学主题")
        if not isinstance(keywords, str) or len(keywords) > 200:
            raise KnowledgeError("补充关键词最多 200 个字符")
        if (
            any(type(v) is not int for v in (year_from, year_to, page, page_size))
            or not 1900 <= year_from <= year_to <= datetime.now(timezone.utc).year
            or not 1 <= page <= 100
            or not 1 <= page_size <= 20
        ):
            raise KnowledgeError("年份或分页参数不正确")
        if type(open_access) is not bool:
            raise KnowledgeError("开放获取选项必须为布尔值")
        # 将补充词作为普通词项，不允许它关闭主题、年份或开放获取限制。
        words = re.findall(r"[\w-]+", keywords, flags=re.UNICODE)
        extra = " AND ".join('TITLE_ABS:"' + word + '"' for word in words)
        query = f"({TOPICS[topic]['query']}) AND SRC:MED AND FIRST_PDATE:[{year_from}-01-01 TO {year_to}-12-31]"
        if extra:
            query += " AND (" + extra + ")"
        if open_access:
            query += " AND OPEN_ACCESS:Y"
        raw = self._get(
            "/search",
            {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": page_size,
                "page": page,
                "sort": "FIRST_PDATE_D desc",
            },
        )
        try:
            data = json.loads(raw)
            items = [
                self._record(row)
                for row in data.get("resultList", {}).get("result", [])
                if str(row.get("id", "")).isdigit()
            ]
            return {
                "items": items,
                "total": int(data.get("hitCount", 0)),
                "page": page,
                "page_size": page_size,
                "query": query,
                "source": "Europe PMC / PubMed",
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        except (ValueError, TypeError) as exc:
            raise ProviderError("文献检索服务返回格式不正确") from exc

    @staticmethod
    def _record(row):
        pmid = str(row.get("pmid") or row.get("id") or "")
        pmcid = str(row.get("pmcid") or "")
        return {
            "pmid": pmid,
            "pmcid": pmcid,
            "doi": row.get("doi", ""),
            "title": plain(row.get("title")),
            "authors": plain(row.get("authorString")),
            "journal": plain(
                (row.get("journalInfo") or {}).get("journal", {}).get("title")
                or row.get("journalTitle")
            ),
            "year": str(row.get("pubYear", "")),
            "abstract": plain(row.get("abstractText")),
            "open_access": row.get("isOpenAccess") == "Y",
            "license": plain(row.get("license")),
            "url": f"https://europepmc.org/article/MED/{pmid}",
        }

    def _save_source(self, name, raw):
        folder = self.settings.data_dir / "sources" / "europepmc"
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(raw).hexdigest()
        target = folder / f"{name}-{digest[:16]}"
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(raw)
        return digest

    @staticmethod
    def parse_fulltext(raw: bytes, record: dict) -> tuple[str, str]:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ProviderError("全文 XML 格式无效") from exc
        meta = root.find("./front/article-meta")
        if meta is None:
            raise KnowledgeError("全文缺少来源标识")
        ids = {
            node.get("pub-id-type"): plain("".join(node.itertext()))
            for node in meta.findall("article-id")
        }
        if ids.get("pmid") != record["pmid"] or ids.get("pmcid") != record["pmcid"]:
            raise KnowledgeError("全文标识与所选论文不一致，已停止入库")
        licenses = " ".join(
            ET.tostring(node, encoding="unicode") for node in meta.findall(".//license")
        )
        urls = re.findall(
            r"https?://creativecommons.org/(?:licenses/by/[\d.]+|publicdomain/zero/[\d.]+)/?",
            licenses,
        )
        if not urls:
            raise KnowledgeError("全文未找到明确的 CC BY 或 CC0 许可")
        body = root.find("body")
        if body is None:
            raise KnowledgeError("全文缺少正文")
        blocks = []

        def collect(node):
            if node.tag in {"p", "title", "table-wrap"}:
                text = plain("".join(node.itertext()))
                if text:
                    blocks.append(text)
                return
            if node.tag in {"fig", "supplementary-material"}:
                return
            for child in node:
                collect(child)

        collect(body)
        text = "\n\n".join(blocks)
        if len(text) < 500:
            raise KnowledgeError("全文正文过短，请检查原文")
        return text, urls[0]

    def import_article(self, pmid, topic="mitochondria", include_fulltext=True, progress=None):
        if (
            not isinstance(pmid, str)
            or not re.fullmatch(r"[0-9]{1,10}", pmid)
            or topic not in TOPICS
            or type(include_fulltext) is not bool
        ):
            raise KnowledgeError("论文编号、主题或全文选项不正确")
        notify = progress or (lambda stage, value: None)
        notify("核对论文来源", 10)
        raw = self._get(
            "/search",
            {"query": f"EXT_ID:{pmid} AND SRC:MED", "format": "json", "resultType": "core"},
        )
        try:
            rows = json.loads(raw).get("resultList", {}).get("result", [])
            original = next(row for row in rows if str(row.get("id")) == pmid)
        except (ValueError, TypeError, StopIteration) as exc:
            raise KnowledgeError("Europe PMC 未找到所选 PMID") from exc
        record = self._record(original)
        metadata_sha = self._save_source(f"PMID{pmid}.json", raw)
        text, kind, warnings, license_url = record["abstract"], "abstract", [], record["license"]
        xml_sha = ""
        if include_fulltext:
            if record["open_access"] and re.fullmatch(r"PMC\d+", record["pmcid"]):
                notify("获取并核对开放全文", 25)
                xml = self._get(f"/{record['pmcid']}/fullTextXML")
                xml_sha = self._save_source(record["pmcid"] + ".xml", xml)
                try:
                    text, license_url = self.parse_fulltext(xml, record)
                    kind = "fulltext"
                except KnowledgeError as exc:
                    if "许可" not in str(exc):
                        raise
                    warnings.append("全文许可未满足自动入库要求，本次仅导入摘要")
            else:
                warnings.append("未找到可获取的开放全文，本次仅导入摘要")
        if not text:
            raise KnowledgeError("该论文没有可入库的摘要或许可明确的全文")
        payload = {
            **record,
            "authors": [
                plain(row.get("fullName"))
                for row in original.get("authorList", {}).get("author", [])
            ],
            "content": text,
            "content_type": kind,
            "topic": topic,
            "source": "europe_pmc",
            "source_id": (record["pmcid"] or "PMID" + pmid) + "-" + kind,
            "license": license_url,
            "xml_sha256": xml_sha,
        }
        result = self.ingestion.ingest(
            payload["source_id"] + ".json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            progress=lambda stage, percent: notify(stage, 35 + int(percent * 0.6)),
        )
        return {
            **result,
            "imported_type": kind,
            "warnings": warnings,
            "source_metadata_sha256": metadata_sha,
            "xml_sha256": xml_sha,
            "pmid": pmid,
        }
