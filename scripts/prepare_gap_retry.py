"""从冻结的候选与拒收记录生成未覆盖组合的独立补采清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.expand_open_corpus import ROOT, file_sha256, save_json_atomic, utc_now


def prepare(previous: Path, output: Path, topic: str, year: int) -> dict:
    previous = previous.resolve(strict=True)
    output = output.resolve()
    if not output.is_relative_to((ROOT / "data/research_corpus").resolve()):
        raise ValueError("补采目录必须位于 data/research_corpus 内")
    if (output / "seed_candidates.json").exists():
        raise FileExistsError("补采候选清单已经冻结，不能覆盖")

    seed_path = previous / "seed_candidates.json"
    acquisition_path = previous / "acquisition_report.json"
    discovery_path = previous / "discovery_report.json"
    seeds = json.loads(seed_path.read_text(encoding="utf-8"))
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    groups = [
        group for group in seeds["groups"]
        if group["topic"] == topic and group["year"] == year
    ]
    details = [
        detail for detail in discovery["details"]
        if detail["topic"] == topic and detail["year"] == year
    ]
    if len(groups) != 1 or len(details) != 1:
        raise ValueError("原批次中未找到唯一的主题年份候选及发现记录")
    if any(
        item["topic"] == topic and int(item["year"]) == year
        for item in acquisition["items"]
    ):
        raise ValueError("原批次已采满该组合")

    failures = {
        item["pmcid"]: item
        for item in acquisition["failures"]
        if item.get("topic") == topic and item.get("year") == year
    }
    candidates = [
        pmcid for pmcid in groups[0]["ids"]
        if pmcid not in failures or failures[pmcid].get("retryable", False)
    ]
    if not candidates or len(candidates) > 40:
        raise ValueError("可补采候选数量不在 1–40 范围内")

    output.mkdir(parents=True, exist_ok=True)
    seed = {
        "created_at_utc": utc_now(),
        "discovery_source": "冻结的 Crossref REST 与 PMC ID Converter 候选；仅未尝试或网络失败重试",
        "max_attempts_per_group": len(candidates),
        "groups": [{
            "topic": topic,
            "year": year,
            "q": groups[0]["q"],
            "source_url": groups[0]["source_url"],
            "ids": candidates,
        }],
    }
    save_json_atomic(output / "seed_candidates.json", seed)
    report = {
        "created_at_utc": utc_now(),
        "source_batch": str(previous),
        "source_seed_sha256": file_sha256(seed_path),
        "source_acquisition_sha256": file_sha256(acquisition_path),
        "source_discovery_sha256": file_sha256(discovery_path),
        "source_snapshots": [
            {
                "path": str(previous / "discovery_snapshots" / snapshot["path"]),
                "sha256": snapshot["sha256"],
            }
            for snapshot in details[0]["id_converter_snapshots"]
        ] + [{
            "path": str(previous / "discovery_snapshots" / details[0]["crossref_snapshot"]),
            "sha256": details[0]["crossref_sha256"],
        }],
        "topic": topic,
        "year": year,
        "untried": sum(pmcid not in failures for pmcid in candidates),
        "network_retries": sum(pmcid in failures for pmcid in candidates),
        "candidate_count": len(candidates),
        "seed_manifest_sha256": file_sha256(output / "seed_candidates.json"),
    }
    if any(file_sha256(Path(item["path"])) != item["sha256"] for item in report["source_snapshots"]):
        raise ValueError("原始候选接口快照摘要不一致")
    save_json_atomic(output / "discovery_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    result = prepare(args.previous_batch, args.output, args.topic, args.year)
    print(json.dumps({key: result[key] for key in ("topic", "year", "candidate_count", "untried", "network_retries")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
