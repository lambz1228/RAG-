"""导入中文数据库授权导出的 EndNote Tagged 或 RIS 题录。"""

import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

from .errors import KnowledgeError
from .ingestion import decode_text
from .literature import TOPICS
from .literature_records import archive_record, ingest_record

CHINESE_SOURCES = {
    "cnki": "中国知网（CNKI）",
    "wanfang": "万方数据",
    "vip": "维普",
    "sinomed": "SinoMed",
}
MAX_BYTES = 1024 * 1024
MAX_RECORDS = 100
ENDNOTE = re.compile(r"^%([0-9A-Z@+])(?:[ \t]+(.*)|$)")
RIS = re.compile(r"^([A-Z][A-Z0-9])\s{0,2}-(?:[ \t]*(.*)|$)")


def parse_export(raw: bytes) -> list[dict]:
    if not raw or len(raw) > MAX_BYTES:
        raise KnowledgeError("题录文件须非空且不得超过 1 MB")
    try:
        text = (
            raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else decode_text(raw)
        )
    except UnicodeDecodeError as exc:
        raise KnowledgeError("题录文件编码无效，请另存为 UTF-8") from exc
    records = []
    fields = defaultdict(list)
    last = ""
    for line in text.splitlines():
        line = line.strip()
        match = ENDNOTE.fullmatch(line) or RIS.fullmatch(line)
        if match:
            tag, value = match.group(1), (match.group(2) or "").strip()
            if tag in {"0", "TY", "ER"}:
                if fields:
                    records.append(fields)
                    fields = defaultdict(list)
                last = ""
            else:
                fields[tag].append(value)
                last = tag
        elif line and last:
            fields[last][-1] += " " + line
    if fields:
        records.append(fields)
    if not records or len(records) > MAX_RECORDS:
        raise KnowledgeError("请上传包含 1 至 100 条记录的 EndNote Tagged 或 RIS 文件")
    return records


def import_export(ingestion, settings, raw: bytes, source: str, topic: str, progress=None) -> dict:
    if source not in CHINESE_SOURCES or topic not in TOPICS:
        raise KnowledgeError("请选择支持的中文数据库和生物学主题")
    records = parse_export(raw)
    digest = archive_record(settings.data_dir, source, "export", raw)
    counts = {"abstracts": 0, "reference_stubs": 0, "duplicates": 0}
    failed = []
    total = len(records)
    for number, fields in enumerate(records, 1):
        if progress:
            progress("导入中文文献题录", int((number - 1) * 100 / total))

        def first(*tags):
            return next((v for tag in tags for v in fields.get(tag, ()) if v), "")

        title = first("T", "TI", "T1", "CT")
        if not title:
            failed.append({"record": number, "error": "缺少题名"})
            continue
        year = re.search(r"\b(?:19|20)\d{2}\b", first("D", "PY", "Y1"))
        abstract = "\n\n".join(
            dict.fromkeys(
                value for tag in ("X", "AB", "N2") for value in fields.get(tag, ()) if value
            )
        )
        exported_url = first("U", "UR")
        try:
            url = urlsplit(exported_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.netloc
                or url.username
                or url.password
            ):
                exported_url = ""
        except ValueError:
            exported_url = ""
        record = {
            "title": title,
            "authors": [v for tag in ("A", "AU", "A1") for v in fields.get(tag, ()) if v],
            "journal": first("J", "JO", "JF", "T2", "B"),
            "year": year[0] if year else "",
            "doi": first("R", "DO"),
            "url": exported_url,
            "abstract": abstract,
            "keywords": [v for tag in ("K", "KW") for v in fields.get(tag, ()) if v],
            "source": source,
            "topic": topic,
        }
        key = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:24]
        record["source_id"] = f"{source}-{key}"
        try:
            result = ingest_record(ingestion, record, {"source_metadata_sha256": digest})
        except KnowledgeError as exc:
            failed.append({"record": number, "error": str(exc)})
            continue
        if result["duplicate"]:
            counts["duplicates"] += 1
        else:
            counts["abstracts" if result["imported_type"] == "abstract" else "reference_stubs"] += 1
    return {
        **counts,
        "total": total,
        "failed": failed,
        "source": source,
        "source_metadata_sha256": digest,
    }
