"""从 Europe PMC 抓取生物学论文摘要，扩充 GraphRAG 评估语料。

用法:
    python scripts/fetch_europepmc_corpus.py --per-topic 15
    python scripts/fetch_europepmc_corpus.py --per-topic 15 --stage   # 同时放入 GraphRAG 输入目录
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_DIR = ROOT / "sci-illust-system"
DEFAULT_OUT = SYSTEM_DIR / "web_app" / "data" / "corpus" / "biology" / "europepmc_2026"
API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# 主题与现有教材条目（细胞器、细胞骨架、细胞分裂等）对应
TOPICS = [
    ("mitochondria", '"mitochondrial dynamics" OR "oxidative phosphorylation"'),
    ("mitophagy", 'mitophagy AND (PINK1 OR Parkin OR BNIP3)'),
    ("er_stress", '"endoplasmic reticulum stress" AND "unfolded protein response"'),
    ("er_mito_contact", '"mitochondria-associated membranes" OR "ER-mitochondria contact"'),
    ("golgi", '"Golgi apparatus" AND (trafficking OR glycosylation)'),
    ("lysosome", 'lysosome AND (TFEB OR "lysosomal biogenesis" OR mTORC1)'),
    ("autophagy", 'autophagy AND (ATG5 OR ATG7 OR LC3 OR ULK1)'),
    ("cytoskeleton", '(microtubule OR "actin filament") AND cytoskeleton'),
    ("mitosis", 'mitosis AND ("spindle assembly checkpoint" OR kinetochore)'),
    ("nucleolus_ribosome", '"ribosome biogenesis" OR nucleolus'),
    ("peroxisome", 'peroxisome AND (PEX OR "peroxisomal biogenesis")'),
    ("cilia", '"primary cilia" OR ciliogenesis'),
]

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def search(query: str, page_size: int) -> list[dict]:
    full_query = f"({query}) AND HAS_ABSTRACT:y AND LANG:eng AND SRC:MED AND PUB_YEAR:[2021 TO 2026]"
    params = urllib.parse.urlencode(
        {"query": full_query, "resultType": "core", "format": "json", "pageSize": page_size}
    )
    with urllib.request.urlopen(f"{API}?{params}", timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("resultList", {}).get("result", [])


def safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", value).strip(". ")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned[:120] or fallback)


def to_record(item: dict, topic: str) -> dict | None:
    abstract = clean_text(item.get("abstractText", ""))
    title = clean_text(item.get("title", ""))
    if len(abstract) < 600 or not title:
        return None
    keywords = (item.get("keywordList") or {}).get("keyword", []) or []
    return {
        "id": str(item.get("pmid") or item.get("id")),
        "source": "europe_pmc",
        "topic": topic,
        "title": title,
        "content": abstract,
        "keywords": keywords,
        "journal": (item.get("journalInfo") or {}).get("journal", {}).get("title", ""),
        "year": str(item.get("pubYear", "")),
        "url": f"https://europepmc.org/article/MED/{item.get('pmid') or item.get('id')}",
        "type": "article",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-topic", type=int, default=15)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--stage", action="store_true", help="同时放入 GraphRAG 输入目录")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = {p.stem.split("__", 1)[0] for p in out_dir.glob("*.json")}
    saved: list[Path] = []

    for topic, query in TOPICS:
        try:
            items = search(query, args.per_topic * 2)
        except Exception as exc:  # 单个主题失败不影响其他主题
            print(f"[{topic}] 抓取失败: {exc}")
            continue
        count = 0
        for item in items:
            record = to_record(item, topic)
            if not record or record["id"] in seen:
                continue
            seen.add(record["id"])
            stem = f"{record['id']}__{safe_name(record['title'], record['id'])}"
            (out_dir / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            txt = out_dir / f"{stem}.txt"
            txt.write_text(f"{record['title']}\n\n{record['content']}\n", encoding="utf-8")
            saved.append(txt)
            count += 1
            if count >= args.per_topic:
                break
        print(f"[{topic}] 新增 {count} 篇")
        time.sleep(0.5)

    print(f"共新增 {len(saved)} 篇，保存在 {out_dir}")

    if args.stage and saved:
        sys.path.insert(0, str(SYSTEM_DIR))
        from text_kb.graphrag_manager import GraphRAGTextKBManager

        manager = GraphRAGTextKBManager(focus_domain="biology")
        staged = sum(bool(manager.stage_document(str(p), source_label="europe_pmc").get("success")) for p in saved)
        print(f"已放入 GraphRAG 输入目录 {staged} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
