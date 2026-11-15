"""导出可入仓库的复核副本：保留模型答案、来源位置与正文摘要，移除文献长文本。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.experiments import export_review, load_questions, write_json  # noqa: E402


def export(run, dataset, output):
    questions, info = load_questions(dataset)
    protocol_bytes = (run / "protocol.json").read_bytes()
    protocol = json.loads(protocol_bytes)
    if protocol["dataset"]["sha256"] != info["sha256"]:
        raise ValueError("题集与原运行不匹配")
    original = (run / "results.jsonl").read_bytes()
    results = [json.loads(line) for line in original.decode().splitlines() if line.strip()]
    expected = {(item["qid"], arm["name"]) for item in questions for arm in protocol["arms"]}
    if len(results) != len(expected) or {(row["qid"], row["arm"]) for row in results} != expected:
        raise ValueError("原运行不完整，不能导出完整复核副本")
    output.mkdir(parents=True, exist_ok=False)
    (output / "protocol.json").write_bytes(protocol_bytes)
    for row in results:
        for citation in row.get("citations", []):
            citation["text_sha256"] = hashlib.sha256(citation["text"].encode()).hexdigest()
            citation["text"] = ""
    (output / "results.jsonl").write_bytes(
        ("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n").encode()
    )
    export_review(output, questions, results, protocol["seed"])
    report = {
        "scope": "模型答案与来源位置的复核副本；长原文保留于原冻结库。此副本的答卷摘要不得混用原机评分表。",
        "source_results_sha256": hashlib.sha256(original).hexdigest(),
        "source_protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
        "answers": len(results),
        "reviewed_answers": 0,
        "transformation": "仅将citations中的text替换为空并保留其SHA-256；模型answer、mode、上下文范围、原文位置与来源元数据不改写",
    }
    write_json(output / "REVIEW_COPY.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.run, args.dataset, args.output), ensure_ascii=False, indent=2))
