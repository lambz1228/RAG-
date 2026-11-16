"""核对运行与题集绑定后，按语言汇总指定来源召回；不计算答案正确率。"""

import argparse
import hashlib
import json
from pathlib import Path


def summarize(report_path, dataset_path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw)
    if (
        hashlib.sha256(raw).hexdigest() != report["source_dataset_sha256"]
        or not report["source_database_unchanged"]
    ):
        raise ValueError("检索运行与题集不匹配或源库变化")
    items = {row["qid"]: row for row in dataset["items"] if row["answerable"]}
    if len(items) != len(dataset["items"]) or any(
        row.get("language") not in {"zh", "en"} or not row.get("concept_id")
        for row in items.values()
    ):
        raise ValueError("仅支持带概念与语言登记的有出处题集")
    results = {}
    k = report["k"]
    for strategy, arm in report["results"].items():
        details = arm["details"]
        if len(details) != len(items) or {row["qid"] for row in details} != set(items):
            raise ValueError("逐题检索记录不完整")
        grouped = {}
        for language in ("zh", "en", "all"):
            rows = [
                r for r in details if language == "all" or items[r["qid"]]["language"] == language
            ]
            grouped[language] = {
                "queries": len(rows),
                "reference_hits": sum(r["hit"] for r in rows),
                f"Hit@{k}": sum(r["hit"] for r in rows) / len(rows) if rows else None,
                f"MRR@{k}": sum(r["reciprocal_rank"] for r in rows) / len(rows) if rows else None,
                "missed_qids": [r["qid"] for r in rows if not r["hit"]],
            }
        results[strategy] = grouped
    return {
        "scope": "中英成对开发查询，仅统计登记参考来源召回；不是回答正确率或全面可回答率",
        "unique_concepts": len({r["concept_id"] for r in items.values()}),
        "queries": len(items),
        "k": k,
        "source_dataset_sha256": report["source_dataset_sha256"],
        "database_sha256": dataset["database_sha256"],
        "raw_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "embedding_fingerprint": report["embedding_fingerprint"],
        "configuration": report["configuration"],
        "source_database_unchanged": report["source_database_unchanged"],
        "results": results,
        "human_review_status": None,
        "limitations": [
            "中英题是同一概念的成对表达，不是36个独立概念",
            "登记来源不是全部正确来源的穷举，未命中不等于答案无支持",
            "同一开发集参与配置诊断，不能改称独立测试",
            "共用查询缓存且存在并发开发活动，不做速度优劣结论",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.report, args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"queries": result["queries"], "concepts": result["unique_concepts"]}))
