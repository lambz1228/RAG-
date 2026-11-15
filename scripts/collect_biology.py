"""按六个细胞生物学主题采集少量官方文献，写入独立新库与来源清单。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.experiments import write_json  # noqa: E402
from biokb.literature import TOPICS, LiteratureService  # noqa: E402


def collect(output, per_topic=2):
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_dir=output / "database")
    services = build_services(settings)
    client = LiteratureService(services.ingestion, settings)
    records, errors, seen = [], [], set()
    try:
        for topic in TOPICS:
            print("发现文献：" + TOPICS[topic]["label"], flush=True)
            try:
                found = client.search(topic=topic, page_size=min(20, per_topic * 3))
                write_json(output / (topic + "-search.json"), found)
                count = 0
                for row in found["items"]:
                    if row["pmid"] in seen:
                        continue
                    try:
                        result = client.import_article(row["pmid"], topic)
                        records.append(
                            {
                                "pmid": row["pmid"],
                                "topic": topic,
                                "title": row["title"],
                                "imported_type": result["imported_type"],
                                "warnings": result["warnings"],
                                "xml_sha256": result["xml_sha256"],
                            }
                        )
                        seen.add(row["pmid"])
                        count += 1
                        if count >= per_topic:
                            break
                    except Exception as exc:
                        errors.append(
                            {"topic": topic, "pmid": row["pmid"], "error_type": type(exc).__name__}
                        )
            except Exception as exc:
                errors.append({"topic": topic, "error_type": type(exc).__name__})
        report = {
            "scope": "按主题检索的开发语料；不是专家审定的生物学金标准",
            "records": records,
            "errors": errors,
            "stats": services.repository.stats(),
            "corpus": services.repository.corpus_manifest(),
        }
        write_json(output / "collection.json", report)
        return report
    finally:
        services.jobs.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-topic", type=int, default=2, choices=range(1, 11))
    args = parser.parse_args()
    report = collect(args.output, args.per_topic)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if len(report["records"]) else 1)
