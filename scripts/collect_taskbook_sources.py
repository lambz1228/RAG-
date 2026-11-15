"""按登记PMID重建开发采集库；来源更新必须显式作为新版本处理。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.experiments import write_json  # noqa: E402
from biokb.literature import LiteratureService  # noqa: E402


def collect(output, registry, allow_source_updates=False):
    plan = json.loads(registry.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    services = build_services(Settings(data_dir=output / "database"))
    client = LiteratureService(services.ingestion, services.settings)
    records, errors = [], []
    try:
        for item in plan["sources"]:
            try:
                result = client.import_article(item["pmid"], item["topic"])
                matches = (
                    result["xml_sha256"] == item["expected_xml_sha256"]
                    and result["imported_type"] == item["expected_imported_type"]
                )
                records.append(
                    {
                        "pmid": item["pmid"],
                        "imported_type": result["imported_type"],
                        "xml_sha256": result["xml_sha256"],
                        "snapshot_matches": matches,
                        "warnings": result["warnings"],
                    }
                )
                if not matches and not allow_source_updates:
                    errors.append(
                        {
                            "pmid": item["pmid"],
                            "error": "线上来源或可获取类型与登记快照不同，停止复用旧版本标识",
                        }
                    )
            except Exception as exc:
                errors.append({"pmid": item["pmid"], "error_type": type(exc).__name__})
        report = {
            "scope": "开发源重建；原始响应留存，新旧来源摘要分别记录",
            "records": records,
            "errors": errors,
            "ready": not errors and len(records) == len(plan["sources"]),
            "allow_source_updates": allow_source_updates,
        }
        write_json(output / "reconstruction.json", report)
        return report
    finally:
        services.jobs.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--registry", type=Path, default=ROOT / "research/taskbook-dev-v1/sources.json"
    )
    parser.add_argument(
        "--allow-source-updates",
        action="store_true",
        help="允许取得新快照，必须重新冻结题集与实验版本",
    )
    args = parser.parse_args()
    report = collect(args.output, args.registry, args.allow_source_updates)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ready"] else 1)
