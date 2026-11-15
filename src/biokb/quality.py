"""资料类型与可检索性规则；自动检查不冒充人工审核。"""

from __future__ import annotations

import re

from .errors import KnowledgeError

CONTENT_TYPES = {"abstract", "fulltext", "knowledge_note", "reference_stub"}
QUALITY_VERSION = "quality-v1"


def assess_document(text: str, metadata: dict) -> dict:
    declared = metadata.get("content_type")
    if declared is not None and (
        not isinstance(declared, str) or declared and declared not in CONTENT_TYPES
    ):
        raise KnowledgeError("资料类型必须为摘要、全文、知识条目或待补充条目")
    placeholder = len(text) < 250 and bool(
        re.search(r"第\s*[一二三四五六七八九十\d]+\s*章", text)
        and re.search(r"细分内容|涵盖.+等内容", text)
    )
    content_type = (
        "reference_stub"
        if placeholder
        else declared
        or ("abstract" if metadata.get("source") == "europe_pmc" else "knowledge_note")
    )
    excluded = content_type == "reference_stub"
    reasons = ["正文仅为章节或目录说明，缺少可支持回答的知识内容"] if excluded else []
    if excluded and metadata.get("source") == "baidu_baike_online":
        reasons.append("来源标签与正文中的教材说明不一致，需人工核对")
    return {
        "version": QUALITY_VERSION,
        "content_type": content_type,
        "status": "needs_content" if excluded else "unreviewed",
        "eligible": not excluded,
        "reasons": reasons,
        "assessment": "规则自动检查，未经人工学术审核",
    }


def validate_filters(filters: dict | None) -> dict:
    if filters is None:
        return {}
    if not isinstance(filters, dict) or set(filters) - {"topic", "year", "content_type"}:
        raise KnowledgeError("筛选条件仅支持 topic、year、content_type")
    result = {}
    for key, value in filters.items():
        if not isinstance(value, str) or len(value) > 200:
            raise KnowledgeError("筛选条件必须是最多 200 字符的字符串")
        value = value.strip()
        if not value:
            continue
        if key == "content_type" and value not in CONTENT_TYPES:
            raise KnowledgeError("不支持的资料类型")
        if key == "year" and not re.fullmatch(r"\d{4}", value):
            raise KnowledgeError("年份必须为四位数字")
        result[key] = value
    return result


def matches_filters(metadata: dict, filters: dict) -> bool:
    return all(str(metadata.get(key, "")) == value for key, value in filters.items())
