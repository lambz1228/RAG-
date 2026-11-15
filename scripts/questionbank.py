"""独立题集管理：种子导入、机器校验与 SHA-256 冻结登记。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    "题号", "题型", "研究问题", "论文编号", "PMID", "PMCID", "DOI", "论文版本",
    "原文文件SHA256", "标准证据起始字符", "标准证据结束字符", "原子答案要点",
    "无答案或冲突说明", "出题者", "出题时间", "复核者", "复核时间", "裁决记录",
    "开发或正式集", "备注",
]
SETS = ("开发集", "正式集")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = [field for field in FIELDS if field not in (rows[0] if rows else FIELDS)]
    if rows and missing:
        raise ValueError(f"缺少列：{missing}")
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def document_index(database: Path) -> dict[str, dict]:
    """同时按 PMID 与 PMCID 建立文档索引。"""
    index: dict[str, dict] = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT id,title,metadata,text FROM documents"):
            metadata = json.loads(row["metadata"])
            pmid = str(metadata.get("pmid") or "")
            if not pmid and str(metadata.get("id") or "").isdigit():
                pmid = str(metadata["id"])
            pmcid = str(metadata.get("pmcid") or "").upper()
            record = {
                "document_id": row["id"], "title": row["title"],
                "text": row["text"], "content_type": metadata.get("content_type"),
                "pmid": pmid, "pmcid": pmcid,
            }
            if pmid:
                index[pmid] = record
            if pmcid:
                index[pmcid] = record
    return index


def validate(rows: list[dict], index: dict[str, dict] | None) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    paper_sets: dict[str, set] = {}
    for number, row in enumerate(rows, 1):
        qid = (row.get("题号") or "").strip()
        if not qid:
            errors.append(f"第 {number} 行缺少题号")
            continue
        if qid in seen:
            errors.append(f"{qid}：题号重复")
        seen.add(qid)
        question_set = (row.get("开发或正式集") or "").strip()
        if question_set not in SETS:
            errors.append(f"{qid}：开发或正式集必须是 {SETS} 之一")
        paper = (row.get("论文编号") or "").strip() or (row.get("PMID") or "").strip()
        paper_sets.setdefault(paper, set()).add(question_set)
        formal = question_set == "正式集"
        for field in ("题型", "研究问题", "论文版本"):
            if formal and not (row.get(field) or "").strip():
                errors.append(f"{qid}：正式集缺少{field}")
        if formal and not (row.get("出题者") or "").strip():
            errors.append(f"{qid}：正式集缺少出题者")
        no_answer = bool((row.get("无答案或冲突说明") or "").strip())
        if formal and not no_answer and not (row.get("原子答案要点") or "").strip():
            errors.append(f"{qid}：正式集非无答案题缺少原子答案要点")
        start_raw, end_raw = row.get("标准证据起始字符", ""), row.get("标准证据结束字符", "")
        if not no_answer:
            if not start_raw.isdigit() or not end_raw.isdigit():
                errors.append(f"{qid}：非无答案题缺少合法字符区间")
                continue
            start, end = int(start_raw), int(end_raw)
            if end <= start:
                errors.append(f"{qid}：字符区间结束必须大于起始")
                continue
            key = (row.get("PMID") or "").strip() or (row.get("PMCID") or "").strip().upper()
            if index is not None and key:
                document = index.get(key)
                if document is None:
                    errors.append(f"{qid}：库内找不到文档 {key}")
                    continue
                text = document["text"]
                if end > len(text):
                    errors.append(f"{qid}：字符区间超出文档长度 {len(text)}")
                    continue
                recorded = (row.get("原文文件SHA256") or "").strip()
                if recorded and recorded != sha256_text(text[start:end]):
                    errors.append(f"{qid}：原文区间 SHA-256 与库内文本不一致")
    for paper, sets in paper_sets.items():
        if len(sets) > 1:
            errors.append(f"论文 {paper} 同时出现在开发集与正式集")
    return errors


def freeze(rows: list[dict], index: dict[str, dict], database: Path,
           version: str, output_dir: Path) -> dict:
    frozen = []
    for row in rows:
        item = {field: (row.get(field) or "").strip() for field in FIELDS}
        no_answer = bool(item["无答案或冲突说明"])
        key = item["PMID"] or item["PMCID"].upper()
        if not no_answer and key in index:
            document = index[key]
            start, end = int(item["标准证据起始字符"]), int(item["标准证据结束字符"])
            item["库内文档ID"] = document["document_id"]
            item["原文文件SHA256"] = sha256_text(document["text"][start:end])
        item["题目SHA256"] = sha256_text(json.dumps(item, ensure_ascii=False, sort_keys=True))
        frozen.append(item)
    target = output_dir / version
    if target.exists():
        raise FileExistsError(f"版本已存在，停止以免覆盖：{target}")
    target.mkdir(parents=True)
    payload = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) for item in frozen
    ) + "\n"
    (target / "questions.jsonl").write_text(payload, encoding="utf-8")
    manifest = {
        "version": version,
        "frozen_at_utc": utc_now(),
        "status": "frozen",
        "database": str(database),
        "database_sha256": sha256_file(database),
        "question_count": len(frozen),
        "by_set": {name: sum(1 for item in frozen if item["开发或正式集"] == name) for name in SETS},
        "by_type": {},
        "questions_file_sha256": sha256_file(target / "questions.jsonl"),
        "human_review_status": None,
    }
    for item in frozen:
        manifest["by_type"][item["题型"]] = manifest["by_type"].get(item["题型"], 0) + 1
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    registry_path = output_dir / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() \
        else {"versions": []}
    registry["versions"].append({
        "version": version, "frozen_at_utc": manifest["frozen_at_utc"],
        "question_count": manifest["question_count"],
        "questions_file_sha256": manifest["questions_file_sha256"],
    })
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def seed(fragment_cases: Path, output: Path) -> int:
    cases = json.loads(fragment_cases.read_text(encoding="utf-8"))["cases"]
    rows = []
    for case in cases:
        rows.append({
            "题号": case["qid"], "题型": "开发诊断", "研究问题": case["question"],
            "论文编号": case["source_id"], "PMID": "", "PMCID": case["source_id"].split("-")[0],
            "DOI": "", "论文版本": "PMC 全文（v26 快照）",
            "原文文件SHA256": case["quote_sha256"],
            "标准证据起始字符": str(case["start_offset"]),
            "标准证据结束字符": str(case["end_offset"]),
            "原子答案要点": "", "无答案或冲突说明": "",
            "出题者": "开发编写（非独立评审）", "出题时间": "2026-11-05",
            "复核者": "", "复核时间": "", "裁决记录": "",
            "开发或正式集": "开发集",
            "备注": "v26 开发题，仅用于回归与诊断，不计入正式成绩",
        })
    write_csv(output, rows)
    return len(rows)


def papers(database: Path, excluded: set[str], output: Path, min_chars: int) -> int:
    """导出可供出题的全文论文清单，排除开发集已用论文。"""
    rows = []
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT id,title,filename,metadata,text FROM documents"):
            metadata = json.loads(row["metadata"])
            if metadata.get("content_type") != "fulltext":
                continue
            if row["id"] in excluded or metadata.get("pmcid", "").upper() in excluded:
                continue
            rows.append({
                "文档ID": row["id"], "PMID": str(metadata.get("pmid") or metadata.get("id") or ""),
                "PMCID": metadata.get("pmcid") or "", "DOI": metadata.get("doi") or "",
                "题名": row["title"], "期刊": metadata.get("journal") or "",
                "年份": metadata.get("year") or "", "正文字符": len(row["text"]),
                "建议题型": "", "出题者": "", "出题状态": "",
            })
    rows = [row for row in rows if row["正文字符"] >= min_chars]
    rows.sort(key=lambda row: (row["期刊"], row["题名"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seed_cmd = commands.add_parser("seed")
    seed_cmd.add_argument("--fragment-cases", type=Path, required=True)
    seed_cmd.add_argument("--output", type=Path, required=True)
    papers_cmd = commands.add_parser("papers")
    papers_cmd.add_argument("--database", type=Path, required=True)
    papers_cmd.add_argument("--exclude-csv", type=Path, default=None)
    papers_cmd.add_argument("--min-chars", type=int, default=20000)
    papers_cmd.add_argument("--output", type=Path, required=True)
    for name in ("validate", "freeze"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--csv", type=Path, required=True)
        cmd.add_argument("--database", type=Path, required=True)
        if name == "freeze":
            cmd.add_argument("--version", required=True)
            cmd.add_argument("--output-dir", type=Path, default=Path("questionbank"))
    args = parser.parse_args()
    if args.command == "seed":
        count = seed(args.fragment_cases.resolve(strict=True), args.output.resolve())
        print(f"已写入 {count} 行开发集种子：{args.output}")
        return 0
    if args.command == "papers":
        excluded: set[str] = set()
        if args.exclude_csv:
            for row in read_csv(args.exclude_csv.resolve(strict=True)):
                excluded.add((row.get("PMCID") or "").strip().upper())
        count = papers(args.database.resolve(strict=True), excluded,
                       args.output.resolve(), args.min_chars)
        print(f"已导出 {count} 篇候选论文：{args.output}")
        return 0
    database = args.database.resolve(strict=True)
    rows = read_csv(args.csv.resolve(strict=True))
    index = document_index(database)
    errors = validate(rows, index)
    if errors:
        print("校验未通过：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"校验通过：{len(rows)} 题")
    if args.command == "freeze":
        manifest = freeze(rows, index, database, args.version, args.output_dir.resolve())
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
