"""可复现的文本规范化、双语分词与有重叠的分块。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STOP_WORDS = set(
    "the a an and or of to in is are was were for with by on as at from that this these those it be can may how what which does do about please explain describe".split()
)
ALIASES = {
    "线粒体": "mitochondria mitochondrion",
    "细胞核": "nucleus nuclear",
    "内质网": "endoplasmic reticulum",
    "高尔基体": "golgi",
    "溶酶体": "lysosome lysosomal",
    "过氧化物酶体": "peroxisome peroxisomal",
    "自噬": "autophagy autophagic",
    "凋亡": "apoptosis apoptotic",
    "表皮生长因子受体": "egfr",
    "表皮生长因子": "egf",
    "信号通路": "signaling pathway",
    "基因表达": "gene expression",
    "核糖体": "ribosome ribosomal",
}


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFC", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\x00", "")
    value = re.sub(r"[^\S\n]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def tokenize(text: str, expand_aliases: bool = True) -> list[str]:
    value = str(text).casefold()
    if expand_aliases:
        additions = [alias for term, alias in ALIASES.items() if term in value]
        value += " " + " ".join(additions)
    tokens = [
        word for word in re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", value) if word not in STOP_WORDS
    ]
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(run) == 1:
            tokens.append(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    start: int
    end: int
    locator: str


def split_text(text: str, size: int = 800, overlap: int = 120) -> list[Chunk]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("分块长度必须为正，且重叠长度小于块长度")
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            lower = start + max(size // 2, overlap + 1)
            choices = [m.end() for m in re.finditer(r"\n|[。！？.!?](?:\s|$)", text[lower:end])]
            if choices:
                end = lower + choices[-1]
        content = text[start:end]
        left = len(content) - len(content.lstrip())
        right = len(content.rstrip())
        if right > left:
            actual_start, actual_end = start + left, start + right
            chunks.append(
                Chunk(
                    len(chunks),
                    text[actual_start:actual_end],
                    actual_start,
                    actual_end,
                    f"字符 {actual_start + 1}–{actual_end}",
                )
            )
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks
