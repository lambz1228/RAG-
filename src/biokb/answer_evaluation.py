"""汇总真实评审者填写的盲评表；空白、部分评分与运行失败单独保留。"""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

from .errors import KnowledgeError
from .experiments import answer_fingerprint

SCORES = ("事实正确性(0/1/2)", "证据支持(0/1/2)", "完整性(0/1/2)")
REQUIRED = (*SCORES, "拒答恰当性(恰当/漏答/误答)", "无支持断言数", "评分者", "评分时间")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def summarize_reviews(run: Path, review: Path) -> dict:
    protocol = json.loads((run / "protocol.json").read_text(encoding="utf-8"))
    keys = json.loads((run / "协调者密钥_勿分发给盲评者.json").read_text(encoding="utf-8"))
    results = [
        json.loads(line)
        for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    qids = protocol.get("question_ids")
    if qids is None:
        items = protocol.get("dataset", {}).get("manifest", {}).get("items")
        qids = (
            [item.get("qid", item.get("题号")) for item in items]
            if isinstance(items, list)
            else None
        )
    arm_names = [arm["name"] for arm in protocol["arms"]]
    if (
        not isinstance(qids, list)
        or not qids
        or any(not isinstance(qid, str) or not qid for qid in qids)
        or len(set(qids)) != len(qids)
        or len(set(arm_names)) != len(arm_names)
    ):
        raise KnowledgeError("协议缺少完整且唯一的题号或组别登记")
    expected = {(qid, arm) for qid in qids for arm in arm_names}
    result_pairs = {(row["qid"], row["arm"]) for row in results}
    key_pairs = {(row["qid"], row["arm"]) for row in keys}
    if (
        result_pairs != expected
        or key_pairs != expected
        or len(results) != len(expected)
        or len(keys) != len(expected)
    ):
        raise KnowledgeError("答卷不完整或混入其他题目，不能按完整实验汇总")
    by_id = {row["答卷号"]: row for row in keys}
    by_pair = {(row["qid"], row["arm"]): row for row in results}
    if len(by_id) != len(keys) or len(by_pair) != len(results):
        raise KnowledgeError("运行或协调者密钥存在重复编号")
    if any((key["qid"], key["arm"]) not in by_pair for key in keys):
        raise KnowledgeError("协调者密钥与运行记录不一致")
    protocol_sha = hashlib.sha256((run / "protocol.json").read_bytes()).hexdigest()
    for key in keys:
        result = by_pair[(key["qid"], key["arm"])]
        if key.get("protocol_sha256") != protocol_sha or key.get(
            "answer_sha256"
        ) != answer_fingerprint(protocol_sha, result):
            raise KnowledgeError("答卷、密钥或实验协议已变化，请重新核对评分材料")
    grades, seen = {}, set()
    with review.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not set(("答卷号", "答卷摘要", *REQUIRED)).issubset(reader.fieldnames or []):
            raise KnowledgeError("评分表缺少必需列")
        for row in reader:
            blind_id = row["答卷号"]
            if blind_id not in by_id or blind_id in seen:
                raise KnowledgeError("评分表存在未知或重复答卷号")
            seen.add(blind_id)
            if row["答卷摘要"] != by_id[blind_id]["answer_sha256"]:
                raise KnowledgeError("评分表不属于本次答卷：" + blind_id)
            if not any((row.get(field) or "").strip() for field in REQUIRED):
                continue
            if any(not (row.get(field) or "").strip() for field in REQUIRED):
                raise KnowledgeError("部分评分字段未填写：" + blind_id)
            if any(row[field] not in {"0", "1", "2"} for field in SCORES):
                raise KnowledgeError("评分只能为0、1或2：" + blind_id)
            if (
                row["拒答恰当性(恰当/漏答/误答)"] not in {"恰当", "漏答", "误答"}
                or not row["无支持断言数"].isdigit()
            ):
                raise KnowledgeError("拒答评价或无支持断言数不合法：" + blind_id)
            try:
                date.fromisoformat(row["评分时间"][:10])
            except ValueError as exc:
                raise KnowledgeError("评分日期须为ISO日期：" + blind_id) from exc
            key = by_id[blind_id]
            pair = key["qid"], key["arm"]
            result = by_pair[pair]
            if "error_type" in result or result.get("mode") == "provider_fallback":
                raise KnowledgeError("运行失败或模型回退不能计为有效生成答案评分：" + blind_id)
            grades[pair] = row
    arms = {}
    for arm in protocol["arms"]:
        name = arm["name"]
        records = [r for r in results if r["arm"] == name]
        graded = [
            (key, grades[(key["qid"], name)])
            for key in keys
            if key["arm"] == name and (key["qid"], name) in grades
        ]
        complete = bool(records) and len(graded) == len(records)
        supported, negative = [], []
        for key, row in graded:
            expected = key["reference"].get("answerable")
            if expected is True:
                supported.append(row)
            elif expected is False:
                negative.append(row)
        metrics = None
        if complete:
            values = [grade for _, grade in graded]
            metrics = {
                "fact_fully_correct_rate": ratio(
                    sum(r[SCORES[0]] == "2" for r in values), len(values)
                ),
                "answer_complete_rate": ratio(
                    sum(r[SCORES[2]] == "2" for r in values), len(values)
                ),
                "citation_support_rate": None
                if arm["strategy"] == "none"
                else ratio(sum(r[SCORES[1]] == "2" for r in values), len(values)),
                "answers_with_unsupported_claims_rate": ratio(
                    sum(int(r["无支持断言数"]) > 0 for r in values), len(values)
                ),
                "unsupported_claims_total": sum(int(r["无支持断言数"]) for r in values),
                "unanswerable_abstention_appropriate_rate": ratio(
                    sum(r["拒答恰当性(恰当/漏答/误答)"] == "恰当" for r in negative), len(negative)
                ),
                "answerable_false_abstention_rate": ratio(
                    sum(r["拒答恰当性(恰当/漏答/误答)"] == "漏答" for r in supported),
                    len(supported),
                ),
                "negative_question_count": len(negative),
                "supported_question_count": len(supported),
            }
        arms[name] = {
            "planned": len(records),
            "reviewed": len(graded),
            "review_complete": complete,
            "errors": sum("error_type" in r for r in records),
            "provider_fallbacks": sum(r.get("mode") == "provider_fallback" for r in records),
            "modes": dict(Counter(r.get("mode", "error") for r in records)),
            "metrics": metrics,
        }
        arms[name]["status"] = (
            "run_failed"
            if arms[name]["errors"] or arms[name]["provider_fallbacks"]
            else "complete"
            if complete
            else "pending_reviews"
        )
    return {
        "scope": protocol["scope"],
        "dataset_sha256": protocol["dataset"]["sha256"],
        "review_file_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256((run / "protocol.json").read_bytes()).hexdigest(),
        "reviewed_answers": len(grades),
        "planned_answers": len(results),
        "complete": bool(results) and len(grades) == len(results),
        "reviewer_identity_verified": False,
        "note": "仅核对填写完整性，不能证明评分者身份或事实判断；开发题评分不得改称独立测试成绩。无检索组引用支持率不适用。",
        "arms": arms,
    }


def markdown_report(report: dict) -> str:
    lines = [
        "# 回答质量评价",
        "",
        report["scope"],
        "",
        f"已填写评分：{report['reviewed_answers']}/{report['planned_answers']}。",
        "",
        "| 组别 | 已评/计划 | 事实完全正确率 | 含无支持断言的回答比例 |",
        "| --- | --- | --- | --- |",
    ]
    for name, row in report["arms"].items():
        metrics = row["metrics"]
        unavailable = (
            "含运行失败，无法汇总完整指标" if row.get("status") == "run_failed" else "待完整评分"
        )
        factual = f"{metrics['fact_fully_correct_rate']:.1%}" if metrics else unavailable
        hallucinated = (
            f"{metrics['answers_with_unsupported_claims_rate']:.1%}" if metrics else unavailable
        )
        lines.append(
            f"| {name} | {row['reviewed']}/{row['planned']} | {factual} | {hallucinated} |"
        )
    lines.extend(["", report["note"], ""])
    return "\n".join(lines)


def summarize_bundle(bundle: dict, reviews: bytes) -> dict:
    """网页上传仅在临时目录处理，不接受客户端文件系统路径。"""
    if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
        raise KnowledgeError("评分运行包版本不正确")
    if (
        not isinstance(bundle.get("keys"), list)
        or not isinstance(bundle.get("results"), list)
        or not isinstance(bundle.get("protocol_text"), str)
    ):
        raise KnowledgeError("评分运行包缺少协议、答卷或密钥")
    if not 1 <= len(bundle["results"]) <= 2000 or len(bundle["keys"]) != len(bundle["results"]):
        raise KnowledgeError("网页每次支持1—2000份答卷，密钥数量必须一致")
    with tempfile.TemporaryDirectory(prefix="biokb-answer-review-") as temporary:
        root = Path(temporary)
        (root / "protocol.json").write_bytes(bundle["protocol_text"].encode("utf-8"))
        (root / "协调者密钥_勿分发给盲评者.json").write_text(
            json.dumps(bundle["keys"], ensure_ascii=False), encoding="utf-8"
        )
        (root / "results.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in bundle["results"]),
            encoding="utf-8",
        )
        review = root / "reviews.csv"
        review.write_bytes(reviews)
        return summarize_reviews(root, review)
