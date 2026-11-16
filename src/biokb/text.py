"""可复现的文本规范化、双语分词与有重叠的分块。"""

from __future__ import annotations

import hashlib
import json
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


TERM_EXPANSION_VERSION = "cell-biology-terms-v1"
# 通用术语补充与BM25原有分词分开，不包含答案或指定文献。
SEMANTIC_TERMS = {
    "未折叠蛋白反应": "unfolded protein response UPR",
    "内质网应激": "endoplasmic reticulum stress",
    "细胞外基质": "extracellular matrix",
    "中间纤维": "intermediate filaments",
    "主动运输": "active transport",
    "被动运输": "passive transport",
    "易化扩散": "facilitated diffusion",
    "浓度梯度": "concentration gradient",
    "电化学梯度": "electrochemical gradient",
    "细胞骨架": "cytoskeleton",
    "原核细胞": "prokaryotic cells",
    "真核细胞": "eukaryotic cells",
    "线粒体": "mitochondria",
    "过氧化物酶体": "peroxisome",
    "内质网": "endoplasmic reticulum",
    "高尔基体": "Golgi apparatus",
    "细胞膜": "plasma membrane",
    "细胞核": "nucleus",
    "叶绿体": "chloroplast",
    "核小体": "nucleosome",
    "染色质": "chromatin",
    "核仁": "nucleolus",
    "核糖体": "ribosome",
    "微丝": "actin filaments",
    "微管": "microtubules",
    "宏自噬": "macroautophagy",
    "自噬溶酶体": "autolysosome",
    "自噬体": "autophagosome",
    "溶酶体": "lysosome",
    "酸性水解酶": "acid hydrolases",
    "蛋白质运输": "protein trafficking",
    "囊泡运输": "vesicle trafficking",
    "信号转导": "signal transduction",
    "磷酸酶": "phosphatase",
    "激酶": "kinase",
    "磷酸化": "phosphorylation",
    "紧密连接": "tight junction",
    "间隙连接": "gap junction",
    "活化能": "activation energy",
    "自由能": "free energy",
    "卡尔文循环": "Calvin cycle",
    "光合作用": "photosynthesis",
    "细胞呼吸": "cellular respiration",
    "电子传递链": "electron transport chain",
    "氧化磷酸化": "oxidative phosphorylation",
    "细胞周期": "cell cycle",
    "凋亡": "apoptosis",
    "引物": "primer",
    "聚合酶": "polymerase",
    "复制": "replication",
    "转录": "transcription",
    "翻译": "translation",
    "5'帽": "5-prime cap",
    "加帽": "mRNA capping",
    "蛋白质合成": "protein synthesis",
    "融合": "fusion",
}
TERM_EXPANSION_SHA256 = hashlib.sha256(
    json.dumps(SEMANTIC_TERMS, ensure_ascii=False, sort_keys=True).encode()
).hexdigest()


def expand_semantic_query(query: str) -> tuple[str, list[str]]:
    normalized = query.casefold().replace("′", "'").replace("’", "'")
    terms = []
    for source, english in SEMANTIC_TERMS.items():
        if source in normalized and english not in terms:
            terms.append(english)
        if len(terms) == 8:
            break
    return (query + "\nKey terms: " + "; ".join(terms), terms) if terms else (query, [])


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
