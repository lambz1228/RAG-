"""将已取得的教材与论文合并到独立离线新库，归档各层原始来源。"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.errors import KnowledgeError  # noqa: E402


def prepare(sources, output):
    output = output.resolve()
    inputs = []
    for source in sources:
        source = source.resolve(strict=True)
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise KnowledgeError("新库目录不得包含来源目录，或位于来源目录内")
        documents = source / "documents"
        if not documents.is_dir():
            documents = source / "database/uploads"
        if not documents.is_dir() or not list(documents.glob("*.json")):
            raise KnowledgeError("来源目录没有可导入的文献JSON")
        raw = source / "raw"
        if not raw.is_dir():
            raw = source / "database/sources"
        if not raw.is_dir():
            raise KnowledgeError("来源目录缺少原始响应归档")
        inputs.append((documents, raw))
    if not inputs:
        raise KnowledgeError("至少需要一个来源目录")
    output.mkdir(parents=True, exist_ok=False)
    services = build_services(Settings(data_dir=output / "database"))
    records = []
    try:
        for index, (documents, raw) in enumerate(inputs, 1):
            result = services.ingestion.import_directory(documents)
            shutil.copytree(raw, output / "database/sources" / f"layer-{index}")
            records.append({"layer": index, **result})
        report = {
            "scope": "多来源独立新库；仅使用离线哈希向量验证合并，不代表语义检索或回答质量",
            "layers": records,
            "stats": services.repository.stats(),
            "ready": all(not item["failed"] for item in records),
            "human_review_status": None,
        }
        (output / "preparation.json").write_bytes(
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
        )
        return report
    finally:
        services.jobs.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.sources, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ready"] else 1)
