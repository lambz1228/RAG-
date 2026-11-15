"""为任务书开发实验整理独立新库，保留采集源库和明确的纳入排除理由。"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.experiments import write_json  # noqa: E402
from biokb.revisions import create_revision  # noqa: E402


def prepare(source, output):
    create_revision(source, output)
    if (source / "sources").is_dir():
        shutil.copytree(source / "sources", output / "sources")
    services = build_services(Settings(data_dir=output))
    decisions = []
    policy = {
        row["pmid"]: row
        for row in json.loads(
            (ROOT / "research/taskbook-dev-v1/sources.json").read_text(encoding="utf-8")
        )["sources"]
    }
    try:
        for row in services.repository.document_catalog():
            pmid = str(row["metadata"].get("pmid", ""))
            if pmid not in policy:
                raise ValueError("源库含未登记论文，请先核对并新建开发源版本：" + pmid)
            include = policy[pmid]["include"]
            decisions.append(
                {
                    "pmid": pmid,
                    "title": row["title"],
                    "include": include,
                    "reason": policy[pmid]["selection_reason"],
                }
            )
            if not include:
                services.repository.delete_document(row["id"])
        for pmcid, topic in [("PMC12142011", "peroxisome"), ("PMC13065327", "cell_cycle")]:
            path = ROOT / "examples/open_demo" / (pmcid + ".json")
            result = services.ingestion.ingest(path.name, path.read_bytes(), {"topic": topic})
            decisions.append(
                {
                    "document_id": result["document"]["id"],
                    "pmcid": pmcid,
                    "include": True,
                    "reason": "带CC BY许可的现有正文样例，补充PEX和减数分裂机制开发用例；不用于版本关系裁决",
                }
            )
        report = {
            "scope": "技术开发选材，不是专家审定或独立测试语料",
            "decisions": decisions,
            "stats": services.repository.stats(),
            "corpus": services.repository.corpus_manifest(),
        }
        write_json(output / "SELECTION.json", report)
        return report
    finally:
        services.jobs.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), ensure_ascii=False, indent=2))
