"""第 1c 步：把难题里“上述四篇摘要”“结合两篇信息”等依赖出题上下文的说法，改写成用户能直接提问的独立问题。

只改问题措辞，标准出处（gold_doc_ids）不变；原问题保存在 original_question 字段，便于论文中说明。
改写时把标准出处的标题交给模型，让它补上主题限定词，但禁止写出标题本身。

用法: python scripts/rag_eval/rewrite_hard_questions.py
"""

from __future__ import annotations

import json
import re

from common import EVAL_DIR, chat, load_corpus, parse_json_block

CONTEXT_WORDS = re.compile(r"上述|以上|这些摘要|这些研究|这四篇|四篇|两篇|综合.{0,4}摘要|摘要\s*[A-D1-4]|另一研究|结合两篇")

PROMPT = """下面是一道检索评估题，但它引用了出题时的上下文（如“上述四篇摘要”“结合两篇信息”），真实用户不会这样提问。
请把它改写成一个独立、自然的中文问题：
1. 删掉所有指代出题材料的说法（上述、以上、这些摘要、两篇、四篇、另一研究等）。
2. 保留原问题考察的内容和难度，必要时根据下面给出的相关论文标题补充主题限定词（如具体疾病、细胞器、通路），
   但不要写出论文标题原文，也不要提“论文”“摘要”“研究A/B”。
3. 问题仍需要综合多篇文献才能完整回答。
只输出 JSON：{{"question": "..."}}

原问题：{question}
相关论文标题：
{titles}
"""


def main() -> int:
    path = EVAL_DIR / "questions_hard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    titles = {d["doc_id"]: d["title"] for d in load_corpus()}
    model = data.get("generator_model", "deepseek-ai/DeepSeek-V3.2")

    changed = 0
    for item in data["items"]:
        if not CONTEXT_WORDS.search(item["question"]):
            continue
        title_block = "\n".join(f"- {titles.get(g, g)}" for g in item["gold_doc_ids"])
        try:
            new_q = str(parse_json_block(chat([{"role": "user", "content": PROMPT.format(question=item["question"], titles=title_block)}], model, 300))["question"]).strip()
        except Exception as exc:
            print(f"{item['qid']} 改写失败: {exc}")
            continue
        if CONTEXT_WORDS.search(new_q):
            print(f"{item['qid']} 改写后仍含指代措辞，保留原题并标记 keep=false: {new_q[:60]}")
            item["keep"] = False
            continue
        item.setdefault("original_question", item["question"])
        item["question"] = new_q
        changed += 1
        print(f"{item['qid']} -> {new_q[:80]}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"改写 {changed} 题，保留 {sum(it['keep'] for it in data['items'])} / {len(data['items'])} 题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
