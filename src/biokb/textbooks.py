"""开放教材固定来源与CNXML正文解析；教材许可和论文许可分别保留。"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

import requests

from .errors import KnowledgeError, ProviderError
from .literature import plain

REPOSITORY = "openstax/osbooks-biology-bundle"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
CNXML = "http://cnx.rice.edu/cnxml"
MDML = "http://cnx.rice.edu/mdml"
SKIP = {"figure", "media", "image", "table", "note", "exercise", "problem", "solution", "footnote"}


def source_url(revision: str, path: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not (
        path in {"LICENSE", "collections/biology-2e.collection.xml"}
        or re.fullmatch(r"modules/m\d{5,7}/index.cnxml", path)
    ):
        raise KnowledgeError("教材修订或文件路径不正确")
    return f"https://raw.githubusercontent.com/{REPOSITORY}/{revision}/{path}"


def fetch_file(revision: str, path: str) -> bytes:
    url = source_url(revision, path)
    try:
        with requests.get(url, timeout=(10, 40), stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ProviderError("教材来源返回异常状态")
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise KnowledgeError("教材模块超过大小限制")
            return bytes(raw)
    except requests.RequestException as exc:
        raise ProviderError("教材固定来源连接失败") from exc


def local_name(node) -> str:
    return node.tag.rsplit("}", 1)[-1]


def inline_text(node) -> str:
    parts = [node.text or ""]
    for child in node:
        if local_name(child) not in SKIP:
            parts.append(inline_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def parse_module(raw: bytes, item: dict, revision: str) -> dict:
    module_id = item["module_id"]
    path = f"modules/{module_id}/index.cnxml"
    source_url(revision, path)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise KnowledgeError("教材CNXML无法解析") from exc
    namespace = {"c": CNXML, "md": MDML}
    if (
        root.tag != f"{{{CNXML}}}document"
        or root.findtext("c:metadata/md:content-id", namespaces=namespace) != module_id
        or root.get("class") in {"introduction", "preface"}
    ):
        raise KnowledgeError("教材模块标识或章节类型不一致")
    for license_node in root.findall(".//md:license", namespace):
        if license_node.get("url", "").replace("http://", "https://").rstrip(
            "/"
        ) != LICENSE_URL.rstrip("/"):
            raise KnowledgeError("教材模块许可与登记不一致")
    title = plain(root.findtext("c:title", namespaces=namespace))
    if not title or (item.get("title") and title != item["title"]):
        raise KnowledgeError("教材标题与登记不一致")
    content = root.find("c:content", namespace)
    if content is None:
        raise KnowledgeError("教材缺少正文")
    blocks = []

    def collect(node):
        tag = local_name(node)
        if tag in SKIP:
            return
        if tag in {"title", "para", "item", "equation"}:
            text = " ".join(inline_text(node).split())
            if text:
                blocks.append(text)
            return
        for child in node:
            collect(child)

    collect(content)
    glossary = root.find("c:glossary", namespace)
    if glossary is not None:
        for entry in glossary:
            term = entry.find(f"{{{CNXML}}}term")
            meaning = entry.find(f"{{{CNXML}}}meaning")
            if term is not None and meaning is not None:
                blocks.append(" ".join((inline_text(term) + ": " + inline_text(meaning)).split()))
    text = "\n\n".join(blocks)
    if len(text) < 500:
        raise KnowledgeError("教材正文过短")
    section = f"{item['chapter_number']}.{item['section_number']}"
    return {
        "title": f"Biology 2e {section} — {title}",
        "content": text,
        "content_type": "fulltext",
        "source": "openstax_biology_2e",
        "source_id": f"openstax-biology-2e-{module_id}@{revision[:12]}",
        "url": f"https://github.com/{REPOSITORY}/blob/{revision}/{path}",
        "authors": ["OpenStax", "Mary Ann Clark", "Jung Choi", "Matthew Douglas"],
        "license": LICENSE_URL,
        "topic": item["topic"],
        "keywords": ["Biology 2e", item["chapter_title"], title],
        "xml_sha256": hashlib.sha256(raw).hexdigest(),
        "processing_notice": "提取正文与术语文本，移除图片、表格、侧栏、练习及脚注；数学排版转为文本。提取文本继续适用CC BY-NC-SA 4.0，仅供非商业使用，不代表原作者认可本系统。",
    }
