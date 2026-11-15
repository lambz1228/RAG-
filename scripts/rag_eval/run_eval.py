"""第 2 步：在同一评估集上比较多种检索方法，输出 Hit@k、MRR 等指标。

对比的方法：
  bm25           关键词检索（BM25，英文分词 + 中文二元组）
  hash           项目自带的本地哈希向量（LocalHashEmbedder）
  bge-m3         BAAI/bge-m3 云端向量
  qwen3-emb      Qwen/Qwen3-Embedding-8B 云端向量
  qwen3-emb+inst Qwen3-Embedding-8B，查询加任务指令前缀
  graphrag       GraphRAG 本地检索（实体向量 -> 关联文本块 -> 文档）

用法: python scripts/rag_eval/run_eval.py [--methods bm25,hash,bge-m3,qwen3-emb,qwen3-emb+inst,graphrag]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

from common import EVAL_DIR, ROOT, api_key, embed, load_corpus

KS = (1, 3, 5, 10)
INSTRUCTION = "Instruct: Given a biology question, retrieve the paper abstract that answers it\nQuery: "
EMBED_MODELS = {"bge-m3": "BAAI/bge-m3", "qwen3-emb": "Qwen/Qwen3-Embedding-8B", "qwen3-emb+inst": "Qwen/Qwen3-Embedding-8B"}


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9\-]+", text)
    for run in re.findall(r"[一-鿿]+", text):
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(map(len, docs)) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, doc in zip(self.tf, self.docs):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl))
            out.append(s)
        return out


def rank(scores: list[float], doc_ids: list[str]) -> list[str]:
    return [doc_ids[i] for i in sorted(range(len(scores)), key=lambda i: -scores[i])]


def cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def metrics(ranked: dict[str, list[str]], gold: dict[str, str]) -> dict:
    hits = {k: 0 for k in KS}
    rr = 0.0
    for qid, g in gold.items():
        lst = ranked.get(qid, [])
        if g in lst:
            pos = lst.index(g) + 1
            rr += 1 / pos
            for k in KS:
                hits[k] += pos <= k
    n = max(1, len(gold))
    return {**{f"Hit@{k}": round(hits[k] / n, 4) for k in KS}, "MRR": round(rr / n, 4), "n": len(gold)}


def multi_metrics(ranked: dict[str, list[str]], gold: dict[str, list[str]]) -> dict:
    """多出处题：Recall@k = 前 k 篇里命中的标准出处比例；AllHit@k = 前 k 篇覆盖全部出处的题目比例。"""
    n = max(1, len(gold))
    out = {}
    for k in (5, 10):
        recall = all_hit = 0.0
        for qid, golds in gold.items():
            top = set(ranked.get(qid, [])[:k])
            got = sum(g in top for g in golds)
            recall += got / len(golds)
            all_hit += got == len(golds)
        out[f"Recall@{k}"] = round(recall / n, 4)
        out[f"AllHit@{k}"] = round(all_hit / n, 4)
    out["n"] = len(gold)
    return out


def topic_precision(ranked: dict[str, list[str]], topic_docs: dict[str, list[str]], k: int = 10) -> float:
    """宽松指标：前 k 篇中属于题目所在主题的比例（综述题的答案可来自同主题任意论文）。"""
    total = 0.0
    for qid, docs in topic_docs.items():
        top = ranked.get(qid, [])[:k]
        total += sum(d in set(docs) for d in top) / k
    return round(total / max(1, len(topic_docs)), 4)


def dense_rank(questions, docs, model: str, with_instruction: bool, cache_dir: Path) -> dict[str, list[str]]:
    """文档向量按模型缓存，避免重复计费。"""
    doc_ids = [d["doc_id"] for d in docs]
    cache = cache_dir / f"doc_vectors_{model.replace('/', '_')}.json"
    stored = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    missing = [d for d in docs if d["doc_id"] not in stored]
    if missing:
        vecs = embed([d["text"][:6000] for d in missing], model)
        stored.update({d["doc_id"]: v for d, v in zip(missing, vecs)})
        cache.write_text(json.dumps(stored), encoding="utf-8")
    prefix = INSTRUCTION if with_instruction else ""
    qvecs = embed([prefix + q["question"] for q in questions], model)
    return {q["qid"]: rank([cos(qv, stored[i]) for i in doc_ids], doc_ids) for q, qv in zip(questions, qvecs)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", default="bm25,hash,bge-m3,qwen3-emb,qwen3-emb+inst,graphrag")
    parser.add_argument("--set", default="single", choices=["single", "hard"], help="single=单文档事实题，hard=多跳/综述题")
    args = parser.parse_args()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]

    hard = args.set == "hard"
    questions_path = EVAL_DIR / ("questions_hard.json" if hard else "questions.json")
    questions = [q for q in json.loads(questions_path.read_text(encoding="utf-8"))["items"] if q.get("keep", True)]
    docs = load_corpus()
    doc_ids = [d["doc_id"] for d in docs]
    if hard:
        gold_multi = {q["qid"]: q["gold_doc_ids"] for q in questions}
        groups = {t: {q["qid"]: q["gold_doc_ids"] for q in questions if q["type"] == t} for t in ("multihop", "summary")}
        summary_topics = {q["qid"]: q["topic_doc_ids"] for q in questions if q["type"] == "summary"}

        def score(r):
            out = {t: multi_metrics(r, g) for t, g in {"all": gold_multi, **groups}.items()}
            out["summary"]["TopicP@10"] = topic_precision(r, summary_topics)
            return out
    else:
        gold = {q["qid"]: q["gold_doc_id"] for q in questions}
        score = lambda r: metrics(r, gold)  # noqa: E731
    cache_dir = EVAL_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"评估集 {questions_path.name}：{len(questions)} 题，语料 {len(docs)} 篇")

    rankings, timings = {}, {}
    for method in methods:
        start = time.time()
        if method == "bm25":
            bm = BM25([tokenize(d["text"]) for d in docs])
            rankings[method] = {q["qid"]: rank(bm.scores(tokenize(q["question"])), doc_ids) for q in questions}
        elif method == "hash":
            from embedding.local_hash import LocalHashEmbedder

            emb = LocalHashEmbedder(dims=1024)
            dvecs = emb.embed_batch([d["text"] for d in docs])
            rankings[method] = {q["qid"]: rank([cos(emb.embed(q["question"]), v) for v in dvecs], doc_ids) for q in questions}
        elif method in EMBED_MODELS:
            rankings[method] = dense_rank(questions, docs, EMBED_MODELS[method], method.endswith("+inst"), cache_dir)
        elif method == "graphrag":
            out = cache_dir / f"graphrag_rankings_{args.set}.json"
            py = ROOT / ".venv-graphrag" / "Scripts" / "python.exe"
            script = Path(__file__).with_name("graphrag_retrieve.py")
            key = api_key()
            env = {**os.environ, "GRAPHRAG_API_KEY": key, "GRAPHRAG_EMBEDDING_API_KEY": key,
                   "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            subprocess.run([str(py), str(script), str(questions_path), str(out)], check=True, env=env)
            rankings[method] = json.loads(out.read_text(encoding="utf-8"))
        else:
            print(f"未知方法: {method}")
            continue
        timings[method] = round((time.time() - start) / max(1, len(questions)), 3)
        print(f"{method:16s} {score(rankings[method])}  平均 {timings[method]}s/题")

    suffix = "_hard" if hard else ""
    results = {m: {"scores": score(r), "sec_per_query": timings[m]} for m, r in rankings.items()}
    report = {"question_set": questions_path.name, "questions": len(questions), "corpus_docs": len(docs), "results": results}
    if not hard:
        report["per_question"] = {
            q["qid"]: {m: (r.get(q["qid"], []).index(q["gold_doc_id"]) + 1 if q["gold_doc_id"] in r.get(q["qid"], []) else None)
                       for m, r in rankings.items()}
            for q in questions
        }
    (EVAL_DIR / f"results{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if hard:
        cols = [("all", "Recall@5"), ("all", "Recall@10"), ("multihop", "Recall@10"), ("multihop", "AllHit@10"),
                ("summary", "Recall@10"), ("summary", "TopicP@10")]
        head = ["全部 Recall@5", "全部 Recall@10", "多跳 Recall@10", "多跳 两篇全中@10", "综述 Recall@10", "综述 主题精度@10"]
        lines = ["| 方法 | " + " | ".join(head) + " | 秒/题 |", "|---" * (len(head) + 2) + "|"]
        for m, r in results.items():
            lines.append(f"| {m} | " + " | ".join(f"{r['scores'][g][c]:.3f}" for g, c in cols) + f" | {r['sec_per_query']} |")
    else:
        lines = ["| 方法 | " + " | ".join(f"Hit@{k}" for k in KS) + " | MRR | 秒/题 |", "|---" * (len(KS) + 3) + "|"]
        for m, r in results.items():
            s = r["scores"]
            lines.append(f"| {m} | " + " | ".join(f"{s[f'Hit@{k}']:.3f}" for k in KS) + f" | {s['MRR']:.3f} | {r['sec_per_query']} |")
    table = "\n".join(lines)
    (EVAL_DIR / f"results{suffix}.md").write_text(f"评估集 {questions_path.name}：{len(questions)} 题，语料 {len(docs)} 篇\n\n{table}\n", encoding="utf-8")
    print("\n" + table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
