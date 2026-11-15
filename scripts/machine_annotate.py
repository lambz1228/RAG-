"""机器标注题集生成：调用已配置 LLM 按候选论文出题，证据区间程序定位。

口径：输出为机器草稿，未经人工评审，不得作为学术正确率依据；
冻结版本的 manifest 固定 human_review_status 为 null。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.errors import ProviderError  # noqa: E402
from biokb.providers import post_json  # noqa: E402
from scripts.questionbank import document_index, sha256_file, sha256_text, write_csv  # noqa: E402

QUOTAS = {"方法条件": 30, "机制/结论": 24, "跨论文比较": 10, "无答案": 20, "版本/时效": 8}
QID_PREFIX = {"方法条件": "MA-M", "机制/结论": "MA-J", "跨论文比较": "MA-X",
              "无答案": "MA-N", "版本/时效": "MA-V"}
EXCERPT_CHARS = 12000
MODEL_NOTE = "机器标注，未经人工评审，不得作为学术正确率依据"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def chat(settings: Settings, system: str, user: str, max_tokens: int = 2400) -> dict:
    """以 OpenAI 兼容接口请求 JSON 对象；仅支持 openai_compatible 后端。"""
    if settings.llm_backend != "openai_compatible":
        raise ProviderError("机器标注需要 openai_compatible 生成后端")
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if settings.llm_response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    data = post_json(
        settings.llm_base_url.rstrip("/") + "/chat/completions",
        payload, settings.llm_api_key, settings.request_timeout,
    )
    content = str(data["choices"][0]["message"]["content"]).strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ProviderError("模型返回不是 JSON 对象")
    return value


def normalized_map(text: str) -> tuple[str, list[int]]:
    """折叠空白并小写化，返回归一文本及每个字符到原文的偏移映射。"""
    chars: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                mapping.append(index)
        else:
            chars.append(char.lower())
            mapping.append(index)
    return "".join(chars), mapping


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """先精确匹配，再做空白/大小写归一匹配；返回原文字符区间。"""
    quote = quote.strip()
    if not quote:
        return None
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote)
    norm_text, mapping = normalized_map(text)
    norm_quote, _ = normalized_map(quote)
    position = norm_text.find(norm_quote)
    if position < 0 or not norm_quote:
        return None
    begin = mapping[position]
    end = mapping[position + len(norm_quote) - 1] + 1
    return begin, end


def methods_excerpt(text: str) -> str:
    import re

    match = re.search(r"(?i)(materials and methods|experimental procedures|methodology|\bmethods\b)",
                      text[2000:])
    start = 2000 + match.start() if match else max(0, len(text) // 3)
    return text[start:start + EXCERPT_CHARS]


SYSTEM_SINGLE = (
    "你是生物文献评测题出题助手。只依据用户提供的论文正文片段出题，不使用外部知识。"
    "引用片段必须是所给正文的逐字连续拷贝，长度80到300字，不得改写或拼接。"
    "只返回JSON对象：{\"questions\":[{\"question\":中文问题,\"answer_points\":[原子答案要点],\"quote\":逐字引用}]}。"
    "answer_points条数1到4条，每条是可独立判分的最小论断。"
)
PROMPTS = {
    "方法条件": (
        "以下节选自论文《{title}》的方法部分。请出{n}道考查具体方法条件的中文题目"
        "（如细胞系、培养条件、浓度、时长、温度、仪器参数等单点事实），"
        "每题给出原子答案要点和逐字引用片段。正文：\n{excerpt}"
    ),
    "机制/结论": (
        "以下是论文《{title}》的正文开头（含摘要与引言）。请出{n}道考查原文明确支持的"
        "机制或结论的中文题目，每题给出原子答案要点和逐字引用片段。正文：\n{excerpt}"
    ),
}
SYSTEM_CROSS = (
    "你是生物文献评测题出题助手。用户给出两篇论文片段。只依据所给文本出一道跨论文比较题，"
    "指出两篇文献在某个具体论点上的一致或差异。若两篇论文缺少可比较的共同点，"
    "返回{\"questions\":[]}。quote_a与quote_b分别是两篇正文内的逐字连续片段（80到300字）。"
    "只返回JSON：{\"questions\":[{\"question\":...,\"answer_points\":[...],\"quote_a\":...,\"quote_b\":...}]}"
)
SYSTEM_NOANSWER = (
    "你是生物文献评测题出题助手。用户给出一篇论文的标题与开头。请出一道与该论文主题相关、"
    "但所给正文没有提供答案的中文问题（例如询问文中未涉及的物种、处理、指标或机制细节），"
    "并说明库内可能缺少证据的理由。只返回JSON：{\"questions\":[{\"question\":...,\"reason\":...}]}"
)
SYSTEM_VERSION = (
    "你是生物文献评测题出题助手。用户给出一段含版本/更新信息的参考文献条目原文。"
    "请出一道考查版本或时效关系的中文题目，问题必须能仅由该段原文回答。"
    "quote必须是所给原文的逐字连续片段（40到300字）。"
    "只返回JSON：{\"questions\":[{\"question\":...,\"answer_points\":[...],\"quote\":...}]}"
)


class Progress:
    """落盘进度：每个原始候选一行 JSON，重启后按键跳过。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict] = []
        self.keys: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self.items.append(item)
                    self.keys.add(item["key"])

    def add(self, key: str, item: dict) -> None:
        if key in self.keys:
            return
        item["key"] = key
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.items.append(item)
        self.keys.add(key)

    def count(self, qtype: str) -> int:
        return sum(len(item.get("questions") or []) for item in self.items
                   if item["type"] == qtype and not item.get("failed"))


def generate_single(settings, progress, papers, qtype, quota):
    key_prefix = QID_PREFIX[qtype]
    for paper in papers:
        if progress.count(qtype) >= quota:
            return
        key = f"{qtype}:{paper['PMCID']}"
        if key in progress.keys:
            continue
        excerpt = methods_excerpt(paper["text"]) if qtype == "方法条件" else paper["text"][:EXCERPT_CHARS]
        if len(excerpt) < 2000:
            progress.add(key, {"type": qtype, "failed": True, "reason": "正文片段过短",
                               "pmcid": paper["PMCID"]})
            continue
        print(f"[{utc_now()}] 生成{qtype}：{paper['PMCID']} {paper['题名'][:40]}", flush=True)
        try:
            result = chat(settings, SYSTEM_SINGLE,
                          PROMPTS[qtype].format(title=paper["题名"], n=2, excerpt=excerpt))
            questions = result.get("questions") or []
        except Exception as exc:  # 单篇失败不中断
            progress.add(key, {"type": qtype, "failed": True, "reason": f"调用失败：{exc}",
                               "pmcid": paper["PMCID"]})
            continue
        serial = progress.count(qtype)
        kept = []
        for item in questions[:2]:
            if serial + len(kept) >= quota:
                break
            if not isinstance(item, dict) or not item.get("question") or not item.get("quote"):
                continue
            if not 60 <= len(str(item["quote"]).strip()) <= 800:
                continue
            if locate_quote(paper["text"], str(item["quote"])) is None:
                continue
            kept.append({
                "type": qtype, "qid": f"{key_prefix}-{serial + len(kept) + 1:03d}",
                "question": str(item["question"]).strip(),
                "answer_points": [str(p).strip() for p in (item.get("answer_points") or []) if str(p).strip()],
                "quote": str(item["quote"]), "pmcid": paper["PMCID"], "pmid": paper["PMID"],
                "doi": paper["DOI"], "title": paper["题名"],
            })
        progress.add(key, {"type": qtype, "pmcid": paper["PMCID"],
                           "kept": len(kept), "questions": kept})
        time.sleep(0.5)


def generate_cross(settings, progress, papers, quota):
    by_journal: dict[str, list[dict]] = {}
    for paper in papers:
        by_journal.setdefault(paper["期刊"], []).append(paper)
    pairs = []
    for group in by_journal.values():
        for index in range(0, len(group) - 1, 2):
            pairs.append((group[index], group[index + 1]))
    for first, second in pairs:
        if progress.count("跨论文比较") >= quota:
            return
        key = f"跨论文比较v2:{first['PMCID']}:{second['PMCID']}"
        if key in progress.keys:
            continue
        user = (
            f"论文A《{first['题名']}》正文片段：\n{first['text'][:6000]}\n\n"
            f"论文B《{second['题名']}》正文片段：\n{second['text'][:6000]}"
        )
        print(f"[{utc_now()}] 生成跨论文比较：{first['PMCID']} x {second['PMCID']}", flush=True)
        try:
            result = chat(settings, SYSTEM_CROSS, user)
            questions = result.get("questions") or []
        except Exception as exc:
            progress.add(key, {"type": "跨论文比较", "failed": True, "reason": f"调用失败：{exc}"})
            continue
        serial = progress.count("跨论文比较")
        kept = []
        for item in questions[:1]:
            if not isinstance(item, dict) or not all(
                    item.get(field) for field in ("question", "quote_a", "quote_b")):
                continue
            if not (60 <= len(str(item["quote_a"]).strip()) <= 800
                    and 60 <= len(str(item["quote_b"]).strip()) <= 800):
                continue
            if (locate_quote(first["text"], str(item["quote_a"])) is None
                    or locate_quote(second["text"], str(item["quote_b"])) is None):
                continue
            kept.append({
                "type": "跨论文比较", "qid": f"MA-X-{serial + len(kept) + 1:03d}",
                "question": str(item["question"]).strip(),
                "answer_points": [str(p).strip() for p in (item.get("answer_points") or []) if str(p).strip()],
                "quote": str(item["quote_a"]), "quote_b": str(item["quote_b"]),
                "pmcid": first["PMCID"], "pmid": first["PMID"], "doi": first["DOI"],
                "title": first["题名"],
                "pmcid_b": second["PMCID"], "pmid_b": second["PMID"], "title_b": second["题名"],
            })
        progress.add(key, {"type": "跨论文比较", "kept": len(kept), "questions": kept})
        time.sleep(0.5)


def generate_noanswer(settings, progress, papers, quota):
    for paper in papers:
        if progress.count("无答案") >= quota:
            return
        key = f"无答案:{paper['PMCID']}"
        if key in progress.keys:
            continue
        user = f"论文标题：《{paper['题名']}》\n正文开头：\n{paper['text'][:3000]}"
        print(f"[{utc_now()}] 生成无答案题：{paper['PMCID']}", flush=True)
        try:
            result = chat(settings, SYSTEM_NOANSWER, user, max_tokens=800)
            questions = result.get("questions") or []
        except Exception as exc:
            progress.add(key, {"type": "无答案", "failed": True, "reason": f"调用失败：{exc}"})
            continue
        serial = progress.count("无答案")
        kept = []
        for item in questions[:1]:
            if not isinstance(item, dict) or not item.get("question"):
                continue
            kept.append({
                "type": "无答案", "qid": f"MA-N-{serial + len(kept) + 1:03d}",
                "question": str(item["question"]).strip(),
                "reason": str(item.get("reason") or "").strip(),
                "pmcid": paper["PMCID"], "pmid": paper["PMID"], "doi": paper["DOI"],
                "title": paper["题名"],
            })
        progress.add(key, {"type": "无答案", "pmcid": paper["PMCID"],
                           "kept": len(kept), "questions": kept})
        time.sleep(0.5)


def generate_version(settings, progress, relations_path, index, quota):
    relations = json.loads(relations_path.read_text(encoding="utf-8"))
    used = 0
    for relation in relations:
        if progress.count("版本/时效") >= quota:
            return
        doi = (relation.get("target_doi_in_citation") or "").strip()
        if not relation.get("target_found") or not doi:
            continue
        key = f"版本/时效:{relation['source_pmid']}:{doi}"
        if key in progress.keys:
            continue
        hit_doc, position = None, -1
        for record in index.values():
            position = record["text"].lower().find(doi.lower())
            if position >= 0:
                hit_doc = record
                break
        if hit_doc is None:
            progress.add(key, {"type": "版本/时效", "failed": True,
                               "reason": "目标 DOI 未出现在库内任何正文"})
            continue
        window = hit_doc["text"][max(0, position - 150):position + 250]
        user = (
            f"版本关系机器线索：源文献 PMID {relation['source_pmid']} 与目标文献"
            f"《{relation.get('target_title') or ''}》（{doi}）存在 {relation.get('type')} 关系。\n"
            f"库内《{hit_doc['title']}》含该 DOI 的原文片段：\n{window}"
        )
        print(f"[{utc_now()}] 生成版本/时效题：{relation['source_pmid']} -> {doi}", flush=True)
        try:
            result = chat(settings, SYSTEM_VERSION, user, max_tokens=800)
            questions = result.get("questions") or []
        except Exception as exc:
            progress.add(key, {"type": "版本/时效", "failed": True, "reason": f"调用失败：{exc}"})
            continue
        serial = progress.count("版本/时效")
        kept = []
        for item in questions[:1]:
            if not isinstance(item, dict) or not item.get("question") or not item.get("quote"):
                continue
            if not 40 <= len(str(item["quote"]).strip()) <= 800:
                continue
            if locate_quote(hit_doc["text"], str(item["quote"])) is None:
                continue
            kept.append({
                "type": "版本/时效", "qid": f"MA-V-{serial + len(kept) + 1:03d}",
                "question": str(item["question"]).strip(),
                "answer_points": [str(p).strip() for p in (item.get("answer_points") or []) if str(p).strip()],
                "quote": str(item["quote"]), "pmcid": hit_doc.get("pmcid", ""),
                "pmid": hit_doc.get("pmid", ""),
                "doi": doi, "title": hit_doc["title"], "hit_document_id": hit_doc["document_id"],
                "relation": {"source_pmid": relation["source_pmid"], "type": relation.get("type"),
                             "target_title": relation.get("target_title"), "target_doi": doi},
            })
        progress.add(key, {"type": "版本/时效", "kept": len(kept), "questions": kept})
        used += 1
        time.sleep(0.5)
    if used == 0:
        print("版本/时效：无可用关系或全部跳过", flush=True)


def collect_questions(progress) -> list[dict]:
    output = []
    for item in progress.items:
        if item.get("failed"):
            continue
        output.extend(item.get("questions") or [])
    return output


def find_by_document_id(index, document_id):
    for record in index.values():
        if record["document_id"] == document_id:
            return record
    return None


def assemble_rows(questions, index, settings, retrieval_scores) -> list[dict]:
    rows = []
    dropped = []
    for item in questions:
        base = {
            "题号": item["qid"], "题型": item["type"], "研究问题": item["question"],
            "论文编号": item.get("pmcid", ""), "PMID": item.get("pmid", ""),
            "PMCID": item.get("pmcid", ""), "DOI": item.get("doi", ""),
            "论文版本": "PMC 全文（v26 快照）",
            "原子答案要点": "；".join(item.get("answer_points") or []),
            "出题者": f"机器标注({settings.llm_model})", "出题时间": "2026-11-13",
            "复核者": "", "复核时间": "", "裁决记录": "", "开发或正式集": "正式集",
        }
        if item["type"] == "无答案":
            note = f"{MODEL_NOTE}；机器理由：{item.get('reason') or '未提供'}"
            score = retrieval_scores.get(item["qid"])
            if score and score.get("top_score") is not None:
                note += (f"；机器检索最高分={score['top_score']:.4f}"
                         f"（hybrid_rerank top1，仅供人工复核参考）")
            rows.append({**base, "无答案或冲突说明": "库内应无充分证据（机器判断，待人工复核）",
                         "标准证据起始字符": "", "标准证据结束字符": "",
                         "原文文件SHA256": "", "备注": note})
            continue
        record = index.get(item.get("pmcid") or "") or find_by_document_id(
            index, item.get("hit_document_id", ""))
        if record is None:
            dropped.append((item["qid"], "库内找不到文档"))
            continue
        if item["type"] == "版本/时效" and not base["PMCID"]:
            base["论文编号"] = record["document_id"]
        located = locate_quote(record["text"], item["quote"])
        if located is None:
            dropped.append((item["qid"], "引用片段未在库内正文定位"))
            continue
        start, end = located
        if not 30 <= end - start <= 1200:
            dropped.append((item["qid"], f"证据长度越界：{end - start}"))
            continue
        note = MODEL_NOTE
        if item["type"] == "跨论文比较":
            second = index.get(item.get("pmcid_b") or "")
            located_b = locate_quote(second["text"], item["quote_b"]) if second else None
            if located_b is None:
                dropped.append((item["qid"], "第二篇引用片段未定位"))
                continue
            start_b, end_b = located_b
            note += (f"；第二证据 {item['pmcid_b']} 区间 {start_b}-{end_b}"
                     f" SHA256={sha256_text(second['text'][start_b:end_b])[:16]}…")
        if item["type"] == "版本/时效":
            note += f"；版本关系机器线索：{json.dumps(item['relation'], ensure_ascii=False)}"
        rows.append({**base, "标准证据起始字符": str(start), "标准证据结束字符": str(end),
                     "原文文件SHA256": sha256_text(record["text"][start:end]),
                     "无答案或冲突说明": "", "备注": note})
    for qid, reason in dropped:
        print(f"丢弃 {qid}：{reason}", flush=True)
    return rows


def score_noanswer(settings, snapshot_database: Path, questions) -> dict:
    """对无答案题跑 hybrid_rerank 检索，记录 top1 分数作为机器依据。"""
    targets = [item for item in questions if item["type"] == "无答案"]
    if not targets:
        return {}
    retrieval_settings = replace(settings, data_dir=snapshot_database.parent)
    services = build_services(retrieval_settings)
    scores = {}
    for item in targets:
        print(f"[{utc_now()}] 无答案检索核对：{item['qid']}", flush=True)
        try:
            result = services.retrieval.search(item["question"], "hybrid_rerank", top_k=5)
            hits = result["hits"]
            top = hits[0] if hits else None
            scores[item["qid"]] = {
                "top_score": (top.get("rerank_score") if top and top.get("rerank_score") is not None
                              else (top["score"] if top else 0.0)),
                "top_document": top["document_id"] if top else "",
            }
        except Exception as exc:
            scores[item["qid"]] = {"top_score": None, "error": str(exc)}
    return scores


def content_fingerprint(database: Path) -> dict:
    """内容级指纹：行数与 documents 逐行哈希，用于核对备份副本与正式库内容一致。"""
    import hashlib
    import sqlite3
    from contextlib import closing

    digest = hashlib.sha256()
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        counts = {}
        for table in ("documents", "chunks", "embeddings"):
            counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for row_id, text in connection.execute("SELECT id,text FROM documents ORDER BY id"):
            digest.update(row_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(text.encode("utf-8")).digest())
    return {"counts": counts, "documents_sha256": digest.hexdigest()}


def cmd_run(args) -> int:
    import csv as csv_module

    settings = Settings.from_env()
    database = args.database.resolve(strict=True)
    formal_hash = sha256_file(database)
    print(f"正式库 SHA-256：{formal_hash}", flush=True)
    index = document_index(database)
    with args.candidates.open("r", encoding="utf-8-sig", newline="") as handle:
        papers = list(csv_module.DictReader(handle))
    for paper in papers:
        record = index.get((paper.get("PMCID") or "").upper()) or index.get(paper.get("PMID") or "")
        if record is None:
            raise ValueError(f"候选论文不在库内：{paper.get('PMCID')}")
        paper["text"] = record["text"]
    progress = Progress(args.progress)
    quotas = {"方法条件": args.methods, "机制/结论": args.mechanism,
              "跨论文比较": args.cross, "无答案": args.noanswer, "版本/时效": args.version}
    generate_single(settings, progress, papers, "方法条件", quotas["方法条件"])
    generate_single(settings, progress, papers, "机制/结论", quotas["机制/结论"])
    generate_cross(settings, progress, papers, quotas["跨论文比较"])
    generate_noanswer(settings, progress, papers, quotas["无答案"])
    generate_version(settings, progress, args.version_relations.resolve(strict=True),
                     index, quotas["版本/时效"])
    questions = collect_questions(progress)
    if any(item["type"] == "无答案" for item in questions):
        snapshot = args.snapshot.resolve(strict=True)
        formal_fp = content_fingerprint(database)
        snapshot_fp = content_fingerprint(snapshot)
        if formal_fp != snapshot_fp:
            raise ValueError(f"检索核对快照与正式库内容不一致：{formal_fp} != {snapshot_fp}")
        print("快照与正式库内容指纹一致，用于无答案题检索核对", flush=True)
        retrieval_scores = score_noanswer(settings, snapshot, questions)
    else:
        retrieval_scores = {}
    rows = assemble_rows(questions, index, settings, retrieval_scores)
    write_csv(args.output.resolve(), rows)
    print(f"已写出 {len(rows)} 行：{args.output}", flush=True)
    return 0


def cmd_sample(args) -> int:
    import csv as csv_module

    with args.csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv_module.DictReader(handle))
    rng = random.Random(args.seed)
    total = max(1, round(len(rows) * args.ratio))
    chosen = sorted(rng.sample(range(len(rows)), total))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv_module.DictWriter(handle, fieldnames=[
            "题号", "题型", "研究问题", "原子答案要点", "机器备注",
            "人工复核结论", "复核人", "复核时间"])
        writer.writeheader()
        for index_number in chosen:
            row = rows[index_number]
            writer.writerow({
                "题号": row["题号"], "题型": row["题型"], "研究问题": row["研究问题"],
                "原子答案要点": row["原子答案要点"], "机器备注": row["备注"],
                "人工复核结论": "", "复核人": "", "复核时间": "",
            })
    print(f"已写出抽检表 {total}/{len(rows)} 行（随机种子 {args.seed}）：{args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run")
    run_cmd.add_argument("--database", type=Path,
                         default=Path("data/biokb-v26-authors-20261105/knowledge.sqlite3"))
    run_cmd.add_argument("--candidates", type=Path, default=Path("questionbank/正式集候选论文.csv"))
    run_cmd.add_argument("--version-relations", type=Path,
                         default=Path("output/research_quality_v26_20261105/version_relations.json"))
    run_cmd.add_argument("--snapshot", type=Path, default=Path(
        "output/research_quality_v26_20261105/experiment_01/snapshot/knowledge.sqlite3"))
    run_cmd.add_argument("--progress", type=Path,
                         default=Path("output/machine_annotation_20261113/progress.jsonl"))
    run_cmd.add_argument("--output", type=Path, default=Path("questionbank/machine_annotated.csv"))
    for name, default in [("methods", 30), ("mechanism", 24), ("cross", 10),
                          ("noanswer", 20), ("version", 8)]:
        run_cmd.add_argument(f"--{name}", type=int, default=default)
    sample_cmd = commands.add_parser("sample")
    sample_cmd.add_argument("--csv", type=Path, default=Path("questionbank/machine_annotated.csv"))
    sample_cmd.add_argument("--output", type=Path, default=Path("questionbank/机器标注抽检表.csv"))
    sample_cmd.add_argument("--ratio", type=float, default=0.3)
    sample_cmd.add_argument("--seed", type=int, default=20261113)
    args = parser.parse_args()
    if args.command == "run":
        return cmd_run(args)
    return cmd_sample(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
