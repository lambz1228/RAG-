"""将开发题草稿与冻结正文精确对齐，保存区间摘要；不自动获得人工评分。"""

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.corpus import sha256, verify_corpus  # noqa: E402
from biokb.experiments import write_json  # noqa: E402


def build(corpus, specs, output):
    verification = verify_corpus(corpus)
    if not verification["passed"]:
        raise ValueError("冻结语料未通过摘要核对")
    with closing(
        sqlite3.connect((corpus.resolve() / "knowledge.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as conn:
        docs = [
            {"id": r[0], "text": r[1], "metadata": json.loads(r[2])}
            for r in conn.execute("SELECT id,text,metadata FROM documents")
        ]
    lookup = {}
    for doc in docs:
        for field in ("pmid", "pmcid", "source_id"):
            if doc["metadata"].get(field):
                key = str(doc["metadata"][field])
                if key in lookup:
                    raise ValueError("题集需要唯一资料版本：" + key)
                lookup[key] = doc
    source = json.loads(specs.read_text(encoding="utf-8"))
    items, seen = [], set()
    for row in source["items"]:
        if row["qid"] in seen:
            raise ValueError("开发题号重复")
        seen.add(row["qid"])
        item = {k: row[k] for k in ("qid", "question", "reference_answer", "type", "answerable")}
        for key in ("concept_id", "language", "topic"):
            if key in row:
                item[key] = row[key]
        spans = row.get("evidence_specs", [row] if row["answerable"] else [])
        evidence = []
        for span in spans:
            doc = lookup[span["source"]]
            text = doc["text"]
            expected = span.get("expected_document_sha256")
            if expected and hashlib.sha256(text.encode()).hexdigest() != expected:
                raise ValueError("参考资料全文摘要与题目草稿登记不一致")
            start = text.index(span["start_marker"])
            end = text.index(span["end_marker"], start) + len(span["end_marker"])
            evidence.append(
                {
                    "document_id": doc["id"],
                    "start_offset": start,
                    "end_offset": end,
                    "sha256": hashlib.sha256(text[start:end].encode()).hexdigest(),
                    "pmid": doc["metadata"].get("pmid"),
                    "pmcid": doc["metadata"].get("pmcid"),
                    "source_id": doc["metadata"].get("source_id"),
                }
            )
        if not row["answerable"]:
            doi = row["absent_doi"]
            if any(doi.lower() == str(doc["metadata"].get("doi", "")).lower() for doc in docs):
                raise ValueError("库外标识已经存在，不能用作无答案题")
            item["absence_basis"] = {
                "kind": "精确DOI目录缺失，仅验证指定来源身份",
                "doi": doi,
                "checked_documents": len(docs),
            }
        item["evidence"] = evidence
        item["human_review_status"] = None
        items.append(item)
    dataset = {
        "origin": "任务书开发草稿：原文锚点校验不等于领域正确性复核",
        "split": "development",
        "human_review_status": None,
        "database_sha256": sha256(corpus / "knowledge.sqlite3"),
        "corpus_manifest_sha256": sha256(corpus / "CORPUS_MANIFEST.json"),
        "spec_sha256": sha256(specs),
        "items": items,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write((json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    write_json(
        output.with_suffix(".validation.json"),
        {
            "count": len(items),
            "supported": sum(item["answerable"] for item in items),
            "unanswerable": sum(not item["answerable"] for item in items),
            "dataset_sha256": sha256(output),
            "academic_correctness_rate": None,
        },
    )
    return dataset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--specs", type=Path, default=Path("research/taskbook-dev-v1/questions.spec.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.corpus, args.specs, args.output)
    print(
        json.dumps(
            {
                "count": len(result["items"]),
                "split": result["split"],
                "human_review_status": result["human_review_status"],
            },
            ensure_ascii=False,
        )
    )
