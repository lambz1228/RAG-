"""按旧文件名清理规则及完整正文核验生成出处映射，不修改题目或数据库。"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from biokb.text import normalize_text  # noqa: E402


def build_map(dataset_path: Path, database: Path, legacy_input: Path, output: Path) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8-sig"))
    golds = {
        gold
        for item in dataset["items"]
        if item.get("keep") is not False
        for gold in (item.get("gold_doc_ids") or [item["gold_doc_id"]])
    }
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [
            {**dict(row), "metadata": json.loads(row["metadata"])}
            for row in connection.execute(
                "SELECT filename,title,text,metadata,content_hash FROM documents"
            )
        ]
    existing = {str(row["metadata"]["source_id"]) for row in rows}
    aliases, evidence = {}, []
    for gold in sorted(golds - existing):
        # 与历史 GraphRAGTextKBManager._safe_filename 采用相同规则。
        candidates = [
            row
            for row in rows
            if re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._\-]+", "_", Path(row["filename"]).stem).strip(
                "._"
            )
            == gold
        ]
        if len(candidates) != 1:
            raise ValueError(f"旧编号未能唯一映射：{gold}")
        row = candidates[0]
        legacy = (legacy_input / (gold + ".txt")).resolve()
        if not legacy.is_relative_to(legacy_input.resolve()):
            raise ValueError("旧编号包含越界路径")
        raw = legacy.read_bytes()
        old_text = normalize_text(raw.decode("utf-8-sig"))
        if old_text != normalize_text(row["title"] + "\n\n" + row["text"]):
            raise ValueError(f"旧输入与新文献的标题、完整正文不相等：{gold}")
        target = str(row["metadata"]["source_id"])
        aliases[gold] = target
        evidence.append(
            {
                "old_id": gold,
                "source_id": target,
                "legacy_file_sha256": hashlib.sha256(raw).hexdigest(),
                "document_file_sha256": row["content_hash"],
                "normalized_body_sha256": hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
                "verification": "旧文件名规则唯一匹配，旧输入等于标题加完整正文（规范化后）",
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "aliases": len(aliases),
        "dataset_file_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "evidence": evidence,
    }
    output.with_suffix(".evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"aliases": len(aliases), "output": str(output)}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--database", type=Path, default=ROOT / "data/biokb/knowledge.sqlite3")
    parser.add_argument(
        "--legacy-input",
        type=Path,
        default=ROOT / "sci-illust-system/web_app/data/text_kb/biology/graphrag/input",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_map(args.dataset, args.database, args.legacy_input, args.output),
            ensure_ascii=False,
        )
    )
