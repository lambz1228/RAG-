"""只读核对冻结题库、题型缺口与人工复核状态，生成待填写复核表。"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

# 新版建议保留已有 82 题；补齐 14 道比较和 14 道版本题后总数 110。
QUOTAS = {"方法条件": 30, "机制/结论": 25, "跨论文比较": 20, "无答案": 20, "版本/时效": 15}


def audit(bank: Path, development: Path | None = None) -> tuple[dict, list[dict]]:
    manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
    raw = (bank / "questions.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    errors = []
    if hashlib.sha256(raw).hexdigest() != manifest["questions_file_sha256"]:
        errors.append("冻结题集 SHA-256 不匹配")
    if len(rows) != manifest["question_count"]:
        errors.append("冻结题集数量不匹配")
    counts = Counter(row["题型"] for row in rows)
    if dict(counts) != manifest["by_type"]:
        errors.append("题型统计与清单不一致")
    ids = [row["题号"] for row in rows]
    if len(set(ids)) != len(ids):
        errors.append("题号重复")
    for row in rows:
        content = {k: v for k, v in row.items() if k != "题目SHA256"}
        digest = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if digest != row.get("题目SHA256"):
            errors.append(f"题目摘要不匹配：{row['题号']}")

    # 旧开发集没有统一 document_id，优先使用正式编号的集合交集。
    def identities(items):
        result = set()
        for row in items:
            for field in ("PMID", "PMCID", "DOI", "论文编号", "库内文档ID"):
                value = str(row.get(field, "")).strip()
                if value:
                    result.add((field, value))
        return result

    overlap = []
    if development:
        dev = [
            json.loads(line)
            for line in (development / "questions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        overlap = sorted(identities(rows) & identities(dev))
        if overlap:
            errors.append("开发集与正式集存在相同论文标识")
    reviewed = sum(
        bool(row.get("复核者", "").strip() and row.get("复核时间", "").strip()) for row in rows
    )
    deficits = {name: max(0, count - counts[name]) for name, count in QUOTAS.items()}
    report = {
        "version": manifest["version"],
        "integrity_passed": not errors,
        "errors": errors,
        "question_count": len(rows),
        "by_type": dict(counts),
        "human_review_records": reviewed,
        "human_review_status": manifest.get("human_review_status"),
        "human_review_verified": False,
        "proposed_protocol": "v2 草案；未批准、未冻结",
        "target_question_count": sum(QUOTAS.values()),
        "target_by_type": QUOTAS,
        "missing_by_type": deficits,
        "missing_total": sum(deficits.values()),
        "development_overlap": overlap,
        "formal_ready": False,
        "limitations": [
            "不读取原文库，不能核对区间事实或判定库内无答案",
            "复核者字段非空不等于真实人员已经完成复核",
            "跨论文或版本题需要逐篇、逐版本的证据标注；不能以备注替代",
        ],
    }
    return report, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, default=Path("questionbank/v1-machine"))
    parser.add_argument("--development", type=Path, default=Path("questionbank/v0-dev"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report, rows = audit(args.bank, args.development)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (args.output / "题目复核表.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "题号",
            "题型",
            "研究问题",
            "原子答案要点",
            "PMID",
            "PMCID",
            "DOI",
            "标准证据起始字符",
            "标准证据结束字符",
            "证据事实一致(待填)",
            "无答案判定依据(待填)",
            "评审者A",
            "评审者B",
            "评审时间",
            "分歧与裁决",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(not report["integrity_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
