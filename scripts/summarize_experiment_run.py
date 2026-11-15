"""从完整原始运行生成可入仓库的机器摘要；格式、覆盖与事实正确性分别标记。"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.experiments import write_json  # noqa: E402


def summarize(run):
    protocol = json.loads((run / "protocol.json").read_text())
    summary = json.loads((run / "summary.json").read_text())
    raw = (run / "results.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if (
        len(rows) != summary["planned_answers"]
        or summary.get("fatal_error_type")
        or not summary["source_unchanged"]
    ):
        raise ValueError("运行不完整或源库变化，不能输出完整摘要")
    arms = {}
    for arm in protocol["arms"]:
        records = [row for row in rows if row["arm"] == arm["name"]]
        coverage = [
            r["context_span_coverage"]
            for r in records
            if r.get("context_span_coverage") is not None
        ]
        arms[arm["name"]] = {
            "count": len(records),
            "modes": dict(Counter(row.get("mode", "error") for row in records)),
            "errors": sum("error_type" in row for row in records),
            "provider_fallbacks": sum(row.get("mode") == "provider_fallback" for row in records),
            "context_span_coverage_mean": sum(coverage) / len(coverage) if coverage else None,
            "context_span_coverage_n": len(coverage),
            "academic_correctness_rate": None,
        }
    return {
        "scope": "12题开发运行的技术诊断；结构通过与区间覆盖不是正确率或幻觉率",
        "dataset_sha256": protocol["dataset"]["sha256"],
        "database_sha256": protocol["database_sha256"],
        "generation_fingerprint": protocol["generation_fingerprint"],
        "embedding_fingerprint": protocol["embedding_fingerprint"],
        "prompt_version": protocol["prompt_version"],
        "configuration": protocol["configuration"],
        "profile": protocol["profile"],
        "protocol_sha256": hashlib.sha256((run / "protocol.json").read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256(raw).hexdigest(),
        "source_unchanged": summary["source_unchanged"],
        "planned_answers": summary["planned_answers"],
        "completed_records": len(rows),
        "arms": arms,
        "academic_correctness_rate": None,
        "human_review_status": None,
        "limitations": [
            "同一开发题参与提示与输出协议诊断，不是独立测试",
            "模型小、库小，且部分题目点名论文",
            "无答案仅验证虚构DOI缺失，不能代表一般领域无答案",
            "多个诊断运行共用本地服务，耗时不是受控速度比较",
            "结构化引用由程序渲染，不能据此推断事实支持率",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("输出已存在，请保留旧摘要")
    write_json(args.output, report)
    print(
        json.dumps(
            {"completed": report["completed_records"], "arms": report["arms"]},
            ensure_ascii=False,
            indent=2,
        )
    )
