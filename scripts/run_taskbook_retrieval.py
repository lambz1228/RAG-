"""在冻结语料副本上比较BM25、真实向量与混合检索；无答案题不混入文档召回。"""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings, load_env  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.corpus import sha256, verify_corpus  # noqa: E402
from biokb.evaluation import evaluate  # noqa: E402
from biokb.experiments import load_questions, write_json  # noqa: E402


def run(corpus, dataset, output, settings):
    if not verify_corpus(corpus)["passed"]:
        raise ValueError("冻结语料核验失败")
    questions, info = load_questions(dataset)
    database = corpus / "knowledge.sqlite3"
    before = sha256(database)
    if info["manifest"].get("database_sha256") != before:
        raise ValueError("题集和冻结库不匹配")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "snapshot"
    snapshot.mkdir()
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(snapshot / "knowledge.sqlite3")) as copied:
            original.backup(copied)
    services = build_services(replace(settings, data_dir=snapshot))
    try:
        items = []
        for question in questions:
            evidence = question["reference"].get("evidence", [])
            if question["reference"].get("answerable") is False:
                continue
            if not evidence:
                raise ValueError("有答案题缺少标准出处")
            gold = {
                str(services.repository.document(span["document_id"])["metadata"]["source_id"])
                for span in evidence
            }
            items.append(
                {
                    "qid": question["qid"],
                    "question": question["question"],
                    "gold_doc_ids": sorted(gold),
                }
            )
        report = evaluate(
            services.retrieval,
            {
                "items": items,
                "origin": "任务书开发草稿；未经领域复核",
                "split": "development",
                "human_review_status": None,
            },
            ["bm25", "dense", "hybrid"],
            k=5,
        )
        report.update(
            source_dataset_sha256=info["sha256"],
            included_supported_questions=len(items),
            excluded_unanswerable_questions=len(questions) - len(items),
            source_database_unchanged=sha256(database) == before,
        )
        write_json(output / "retrieval.json", report)
        return report
    finally:
        services.jobs.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, help="显式读取模型配置，不覆盖已有环境变量")
    args = parser.parse_args()
    if args.env_file:
        load_env(args.env_file.resolve(strict=True))
    result = run(args.corpus, args.dataset, args.output, Settings.from_env())
    print(
        json.dumps(
            {
                name: {k: v for k, v in row.items() if k != "details"}
                for name, row in result["results"].items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )
