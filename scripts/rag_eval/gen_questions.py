"""第 1 步：由模型根据每篇文档出题，生成带标准出处的评估集。

每篇内容足够长的文档出 1 道题，题目必须只能由该文档回答。
生成后做自动过滤（太短、照抄原句、泄露标题），结果写入 tests/benchmarks/rag_eval/questions.json，
人工抽查后把不合格题目的 "keep" 改成 false 即可。

用法: python scripts/rag_eval/gen_questions.py [--limit 60] [--model Qwen/Qwen2.5-7B-Instruct]
"""

from __future__ import annotations

import argparse
import json
import random

from common import EVAL_DIR, chat, load_corpus, parse_json_block

PROMPT = """你是生物学考试出题人。阅读下面这篇论文摘要，出 1 道检索评估题。
要求：
1. 用中文提问，问题必须能且只能从这篇摘要中找到答案，要考察具体事实（如某个蛋白的作用、某个实验结论）。
2. 不要照抄原文句子，不要在问题里直接写出论文标题。
3. 问题中要带上足够的限定信息（研究对象、疾病或实验体系），使它不会和其他论文混淆。
4. 同时给出简短的参考答案（中文，不超过 60 字）。
只输出 JSON：{{"question": "...", "answer": "..."}}

摘要：
{text}
"""


def too_similar(question: str, text: str) -> bool:
    """问题与原文高度重合（照抄）时判为不合格。"""
    q = question.lower()
    return any(len(chunk) >= 25 and chunk in text.lower() for chunk in [q[i : i + 25] for i in range(0, max(1, len(q) - 25), 5)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=60)
    # 出题用较强的模型：7B 模型的问题偏笼统，且 JSON 格式常出错
    parser.add_argument("--model", default="deepseek-ai/DeepSeek-V3.2")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    docs = [d for d in load_corpus() if len(d["text"]) >= 600]
    random.Random(args.seed).shuffle(docs)
    docs = docs[: args.limit]
    out_path = EVAL_DIR / "questions.json"
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    items = []
    for i, doc in enumerate(docs, 1):
        try:
            data = parse_json_block(chat([{"role": "user", "content": PROMPT.format(text=doc["text"][:3000])}], args.model, 300))
            question, answer = str(data["question"]).strip(), str(data["answer"]).strip()
        except Exception as exc:
            print(f"[{i}] 出题失败 {doc['doc_id'][:40]}: {exc}")
            continue
        reasons = []
        if len(question) < 12:
            reasons.append("问题过短")
        if too_similar(question, doc["text"]):
            reasons.append("照抄原文")
        if doc["title"].lower().rstrip(".")[:40] in question.lower():
            reasons.append("泄露标题")
        items.append({
            "qid": f"q{len(items) + 1:03d}",
            "question": question,
            "reference_answer": answer,
            "gold_doc_id": doc["doc_id"],
            "gold_title": doc["title"],
            "auto_filter": reasons,
            "keep": not reasons,
        })
        print(f"[{i}/{len(docs)}] {'保留' if not reasons else '过滤:' + ','.join(reasons)}  {question[:50]}")

    out_path.write_text(json.dumps({"generator_model": args.model, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    kept = sum(it["keep"] for it in items)
    print(f"共 {len(items)} 题，自动过滤后保留 {kept} 题 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
