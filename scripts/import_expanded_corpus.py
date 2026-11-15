"""将筛选后的开放全文逐篇导入独立语义库，并持久化可续跑的入库清单。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from biokb.config import PROJECT_ROOT, Settings
from biokb.container import build_services
from biokb.errors import KnowledgeError, ProviderError
from scripts.expand_open_corpus import file_sha256, save_json_atomic, utc_now


def import_batch(batch: Path, target: Path) -> dict:
    batch = batch.resolve(strict=True)
    target = target.resolve(strict=True)
    if target in {
        PROJECT_ROOT / "data/biokb",
        PROJECT_ROOT / "data/biokb-v21",
        PROJECT_ROOT / "data/biokb-v21-semantic",
    }:
        raise ValueError("入库目标不能是既有正式或历史知识库")
    curation_path = batch / "curation_report.json"
    curation = json.loads(curation_path.read_text(encoding="utf-8"))
    parent = json.loads((target / "REVISION.json").read_text(encoding="utf-8"))
    if parent["parent_database_sha256"] != curation["source_database_sha256"]:
        raise ValueError("新库的父版本与采集时源库不一致")
    candidates = [entry for entry in curation["entries"] if entry["included"]]
    if len(candidates) != curation["counts"]["included"]:
        raise ValueError("筛选报告数量不一致")
    source_dir = batch / "curated_documents"
    for entry in candidates:
        path = source_dir / f"{entry['pmcid']}.json"
        if not path.is_file() or file_sha256(path) != entry["curated_json_sha256"]:
            raise ValueError(f"待入库文件摘要不一致：{path}")
    report_path = batch / "import_report.json"
    report = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else {
            "created_at_utc": utc_now(),
            "target": str(target),
            "curation_report_sha256": file_sha256(curation_path),
            "items": [],
        }
    )
    if report["target"] != str(target) or report["curation_report_sha256"] != file_sha256(
        curation_path
    ):
        raise ValueError("续跑时目标库或筛选报告发生变化")
    settings = replace(Settings.from_env(), data_dir=target)
    services = build_services(settings)
    try:
        existing_fingerprints = services.repository.stats()["index_fingerprints"]
        fingerprint = services.retrieval.embedder.fingerprint
        if existing_fingerprints != [fingerprint] or settings.embedding_backend == "hash":
            raise ValueError("目标库向量指纹与当前真实模型不一致，拒绝混合索引")
        report["embedding_fingerprint"] = fingerprint
        finished = {item["pmcid"] for item in report["items"] if item["status"] == "imported"}
        for index, entry in enumerate(candidates, 1):
            pmcid = entry["pmcid"]
            if pmcid in finished:
                continue
            path = source_dir / f"{pmcid}.json"
            try:
                result = services.ingestion.ingest(path.name, path.read_bytes())
            except (KnowledgeError, ProviderError) as exc:
                report["items"].append(
                    {
                        "pmcid": pmcid,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "attempted_at_utc": utc_now(),
                    }
                )
                save_json_atomic(report_path, report)
                if isinstance(exc, ProviderError):
                    raise
                print(f"[{index}/{len(candidates)}] {pmcid} 入库失败：{exc}", flush=True)
                continue
            report["items"].append(
                {
                    "pmcid": pmcid,
                    "pmid": entry["pmid"],
                    "status": "imported",
                    "duplicate": result["duplicate"],
                    "document_id": result["document"]["id"],
                    "chunk_count": result.get("chunk_count")
                    or len(services.repository.document(result["document"]["id"])["chunks"]),
                    "json_sha256": entry["curated_json_sha256"],
                    "completed_at_utc": utc_now(),
                }
            )
            save_json_atomic(report_path, report)
            finished.add(pmcid)
            print(
                f"[{index}/{len(candidates)}] {pmcid} 已入库"
                f"（{'既有相同文件' if result['duplicate'] else '新增'}）",
                flush=True,
            )
        report["completed_at_utc"] = utc_now()
        report["counts"] = {
            "expected": len(candidates),
            "imported": len(finished),
            "failed": len(candidates) - len(finished),
            "database": services.repository.stats(),
        }
        save_json_atomic(report_path, report)
        return report
    finally:
        services.jobs.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    result = import_batch(args.batch, args.target)
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
    return 0 if result["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
