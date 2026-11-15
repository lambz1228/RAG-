"""同语料运行离线/真实模型检索实验，先开发选择再冻结评测。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.evaluation import evaluate, write_report  # noqa: E402


def run(baseline: Path, semantic: Path, datasets: Path, output: Path) -> None:
    settings = Settings.from_env()
    if settings.embedding_backend == "hash" or settings.reranker_backend == "disabled":
        raise ValueError("请先配置并验证真实向量及重排序模型")
    offline = build_services(
        replace(
            settings,
            data_dir=baseline.resolve(),
            embedding_backend="hash",
            embedding_model="local-hash-v2",
            embedding_revision="",
            llm_backend="disabled",
            reranker_backend="disabled",
        )
    )
    online = build_services(
        replace(
            settings,
            data_dir=semantic.resolve(),
            request_timeout=max(90, settings.request_timeout),
        )
    )
    try:
        assert offline.repository.corpus_manifest() == online.repository.corpus_manifest(), (
            "语料不一致"
        )

        def chunks(service):
            return [
                (r["id"], r["text"], r["locator"], r["start_offset"], r["end_offset"])
                for r in service.repository.index_rows()
            ]

        assert chunks(offline) == chunks(online), "分块不一致"
        output.mkdir(parents=True, exist_ok=False)
        methods = ["bm25", "dense", "hybrid", "hybrid_rerank"]
        protocol = {
            "selection_rule": "仅按开发集 Hit@10 最大，再按 MRR@10 最大；完全相同按方法列表先后选择",
            "methods": methods,
            "k": 10,
            "corpus": online.repository.corpus_manifest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "limitations": "旧模型生成题的分组复用，未经人工复核，非全新未见测试集；网络延迟不用于选型",
        }
        write_report(protocol, output / "protocol.json")
        for split in ["development", "evaluation"]:
            dataset = json.loads((datasets / f"{split}.json").read_text(encoding="utf-8"))
            for profile, services, selected in [
                ("hash", offline, methods[:3]),
                ("semantic", online, methods),
            ]:
                print(f"开始 {split} / {profile}：{len(dataset['items'])} 题", flush=True)
                report = evaluate(services.retrieval, dataset, selected, 10)
                write_report(report, output / f"{split}_{profile}.json")
                print(
                    json.dumps(
                        {
                            "split": split,
                            "profile": profile,
                            "metrics": {
                                name: {
                                    key: value for key, value in metrics.items() if key != "details"
                                }
                                for name, metrics in report["results"].items()
                            },
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if split == "development" and profile == "semantic":
                    best = max(
                        methods,
                        key=lambda name: (
                            report["results"][name]["Hit@10"],
                            report["results"][name]["MRR@10"],
                        ),
                    )
                    write_report(
                        {
                            **protocol,
                            "selected_strategy": best,
                            "development_dataset_sha256": report["dataset_sha256"],
                            "frozen_at": datetime.now(timezone.utc).isoformat(),
                        },
                        output / "selection.json",
                    )
    finally:
        offline.jobs.close()
        online.jobs.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--semantic", required=True, type=Path)
    parser.add_argument("--datasets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.baseline, args.semantic, args.datasets, args.output)
