"""第 1b 步：生成跨文档难题，补充单文档事实题，用来体现 GraphRAG 的优势。

两类题：
  multihop  多跳题：从同一主题选两篇论文，答案必须结合两篇才能得出，标准出处为两篇
  summary   主题综述题：给出同一主题的 4 篇论文，问它们的共性或对比，标准出处为这 4 篇

输出 tests/benchmarks/rag_eval/questions_hard.json，评估时按“标准出处的召回率”计分。
用法: python scripts/rag_eval/gen_hard_questions.py [--per-type 20]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict

from common import EVAL_DIR, ROOT, chat, load_corpus, parse_json_block

META_DIR = ROOT / "sci-illust-system" / "web_app" / "data" / "corpus" / "biology" / "europepmc_2026"

MULTIHOP_PROMPT = """你是生物学考试出题人。下面是同一领域的两篇论文摘要 A 和 B。
请出 1 道中文“多跳”检索题：
1. 必须同时用到 A 和 B 中的信息才能完整回答，只看其中一篇答不全。
   例如：A 中某分子/机制与 B 中某现象的联系，或比较 A、B 两个研究体系的异同。
2. 问题里不要出现论文标题，不要照抄原句，但要带上能定位到这两篇论文的关键信息（研究对象、分子名称、疾病）。
3. 给出不超过 80 字的参考答案。
如果这两篇摘要确实找不到能联系起来的点，输出 {{"skip": true}}。
否则只输出 JSON：{{"question": "...", "answer": "..."}}

摘要 A：
{a}

摘要 B：
{b}
"""

SUMMARY_PROMPT = """你是生物学考试出题人。下面是同一主题的 {n} 篇论文摘要（编号 1 到 {n}）。
请出 1 道中文“主题综述”检索题：
1. 问题询问这些研究共同涉及的机制、分子或结论，或者它们之间的对比；答案分散在多篇摘要中。
2. 问题不要写成只针对某一篇论文的细节题，也不要出现论文标题。
3. 给出不超过 100 字的参考答案，并列出答案实际用到了哪些编号的摘要（至少 3 篇）。
只输出 JSON：{{"question": "...", "answer": "...", "used": [1, 2, 3]}}

{docs}
"""


def topic_groups(docs: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for doc in docs:
        meta = META_DIR / f"{doc['doc_id']}.json"
        if meta.exists() and len(doc["text"]) >= 600:
            groups[json.loads(meta.read_text(encoding="utf-8"))["topic"]].append(doc)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-type", type=int, default=20)
    parser.add_argument("--model", default="deepseek-ai/DeepSeek-V3.2")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    groups = topic_groups(load_corpus())
    topics = sorted(groups)
    items: list[dict] = []

    # 多跳题：轮流在各主题内随机挑两篇，模型判断无关联时跳过重抽
    attempts = 0
    while sum(it["type"] == "multihop" for it in items) < args.per_type and attempts < args.per_type * 3:
        topic = topics[attempts % len(topics)]
        attempts += 1
        a, b = rng.sample(groups[topic], 2)
        try:
            data = parse_json_block(chat([{"role": "user", "content": MULTIHOP_PROMPT.format(a=a["text"][:2500], b=b["text"][:2500])}], args.model, 400))
        except Exception as exc:
            print(f"[多跳] 失败: {exc}")
            continue
        if data.get("skip") or not data.get("question"):
            print(f"[多跳] {topic} 两篇无关联，跳过")
            continue
        items.append({
            "qid": f"h{len(items) + 1:03d}", "type": "multihop", "topic": topic,
            "question": str(data["question"]).strip(), "reference_answer": str(data.get("answer", "")).strip(),
            "gold_doc_ids": [a["doc_id"], b["doc_id"]], "keep": True,
        })
        print(f"[多跳 {sum(it['type'] == 'multihop' for it in items)}] {topic}: {items[-1]['question'][:60]}")

    # 主题综述题：每个主题取 4 篇，按模型标注的实际用到的摘要作为标准出处
    for i in range(args.per_type):
        topic = topics[i % len(topics)]
        picked = rng.sample(groups[topic], min(4, len(groups[topic])))
        block = "\n\n".join(f"摘要 {j + 1}：\n{d['text'][:1800]}" for j, d in enumerate(picked))
        try:
            data = parse_json_block(chat([{"role": "user", "content": SUMMARY_PROMPT.format(n=len(picked), docs=block)}], args.model, 500))
            used = [picked[int(k) - 1]["doc_id"] for k in data.get("used", []) if 1 <= int(k) <= len(picked)]
        except Exception as exc:
            print(f"[综述] 失败: {exc}")
            continue
        if len(set(used)) < 3:
            print(f"[综述] {topic} 用到的摘要少于 3 篇，跳过")
            continue
        items.append({
            "qid": f"h{len(items) + 1:03d}", "type": "summary", "topic": topic,
            "question": str(data["question"]).strip(), "reference_answer": str(data.get("answer", "")).strip(),
            "gold_doc_ids": sorted(set(used)), "keep": True,
        })
        print(f"[综述 {sum(it['type'] == 'summary' for it in items)}] {topic}: {items[-1]['question'][:60]}")

    out = EVAL_DIR / "questions_hard.json"
    out.write_text(json.dumps({"generator_model": args.model, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"多跳 {sum(it['type'] == 'multihop' for it in items)} 题，综述 {sum(it['type'] == 'summary' for it in items)} 题 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
