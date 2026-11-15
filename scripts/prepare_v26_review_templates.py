"""生成独立证据题与真实领域复核所需的空白工作表。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relations", type=Path, required=True)
    parser.add_argument("--fragment-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    relations = json.loads(args.relations.read_text(encoding="utf-8"))
    cases = json.loads(args.fragment_cases.read_text(encoding="utf-8"))["cases"]
    write_csv(output / "独立证据题集_空白模板.csv", [
        "题号", "题型", "研究问题", "论文编号", "PMID", "PMCID", "DOI", "论文版本",
        "原文文件SHA256", "标准证据起始字符", "标准证据结束字符", "原子答案要点",
        "无答案或冲突说明", "出题者", "出题时间", "复核者", "复核时间", "裁决记录",
        "开发或正式集", "备注",
    ], [])
    write_csv(output / "全文开发题_人工复核空白.csv", [
        "题号", "目标全文出处", "模型回答正确性0到2", "证据支持0到2", "完整性0到2",
        "拒答是否恰当", "无支持断言", "原文核查位置", "评审者", "复核时间", "裁决备注",
    ], [{"题号": case["qid"], "目标全文出处": case["source_id"]} for case in cases])
    write_csv(output / "版本关联_人工裁决空白.csv", [
        "源PMID", "关系类型", "目标PMID", "目标题名", "目标记录已核对", "关系机器警报",
        "原文受影响范围", "处理决定", "裁决理由", "评审者", "复核时间", "二审者", "分歧记录",
    ], [{
        "源PMID": row["source_pmid"], "关系类型": row["type"],
        "目标PMID": row["target_pmid"], "目标题名": row["target_title"],
        "目标记录已核对": "是" if row["target_found"] else "否",
        "关系机器警报": "是" if row["machine_alert"] else "否",
    } for row in relations])
    print(f"已生成空白独立题模板、{len(cases)} 行全文题复核表和 {len(relations)} 行版本关系裁决表")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
