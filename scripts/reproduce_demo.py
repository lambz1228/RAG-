"""离线核验并重建开放文献演示库；不读取 .env，不改写已有数据库。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.experiments import run_experiment, write_json  # noqa: E402


def reproduce(output: Path, source: Path = ROOT / "examples/open_demo") -> dict:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if (
            Path(name).name != name
            or hashlib.sha256((source / name).read_bytes()).hexdigest() != expected
        ):
            raise ValueError("示例文件摘要不匹配：" + name)
    output.mkdir(parents=True, exist_ok=False)
    services = build_services(Settings(data_dir=output / "database"))
    items = []
    try:
        for article in manifest["articles"]:
            path = source / article["record_file"]
            result = services.ingestion.ingest(path.name, path.read_bytes())
            doc = services.repository.document(result["document"]["id"])
            # 每篇第一段的原文引用用于定位回归，题目是开发查询，未作领域正确性评价。
            text = doc["text"]
            end = text.find("\n\n")
            quote = text[:end] if end > 0 else text[:200]
            items.append(
                {
                    "qid": article["pmcid"] + "-demo",
                    "type": "原文定位开发查询",
                    "question": "Summarize the introduction of " + doc["title"],
                    "evidence": [
                        {
                            "document_id": doc["id"],
                            "start_offset": 0,
                            "end_offset": len(quote),
                            "sha256": hashlib.sha256(quote.encode()).hexdigest(),
                        }
                    ],
                }
            )
        report = {
            "scope": manifest["scope"],
            "stats": services.repository.stats(),
            "corpus": services.repository.corpus_manifest(),
            "academic_correctness_rate": None,
        }
        write_json(
            output / "dataset.json",
            {
                "origin": "从示例正文构建的开发查询；不计入正式集",
                "human_review_status": None,
                "items": items,
            },
        )
        write_json(output / "reproduction.json", report)
    finally:
        services.jobs.close()
    report["offline_experiment"] = run_experiment(
        output / "database/knowledge.sqlite3",
        output / "dataset.json",
        output / "experiment",
        Settings(),
        offline=True,
    )
    write_json(output / "reproduction.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(reproduce(parser.parse_args().output), ensure_ascii=False, indent=2))
