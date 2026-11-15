"""按共享标准出处分组冻结开发/评测题；旧题不冒充独立人工金标准。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def prepare(paths: list[Path], output: Path, source_map: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    mapping = json.loads(source_map.read_text(encoding="utf-8")) if source_map else {}
    items, origins = [], []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        origins.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "generator_model": data.get("generator_model"),
            }
        )
        for index, original in enumerate(data["items"]):
            if original.get("keep") is False:
                continue
            gold = original.get("gold_doc_ids") or [original["gold_doc_id"]]
            items.append(
                {
                    **original,
                    "qid": path.stem + ":" + str(original.get("qid", index + 1)),
                    "original_gold": gold,
                    "gold_doc_ids": [mapping.get(value, value) for value in gold],
                    "review": {
                        "status": "pending",
                        "reviewer": "",
                        "evidence_quotes": [],
                        "reference_answer": "",
                        "notes": "",
                    },
                }
            )
    groups = []
    for item in items:
        matched = [group for group in groups if group["sources"] & set(item["gold_doc_ids"])]
        if not matched:
            groups.append({"sources": set(item["gold_doc_ids"]), "items": [item]})
        else:
            merged = {"sources": set(item["gold_doc_ids"]), "items": [item]}
            for group in matched:
                merged["sources"].update(group["sources"])
                merged["items"].extend(group["items"])
                groups.remove(group)
            groups.append(merged)
    groups.sort(
        key=lambda group: hashlib.sha256("|".join(sorted(group["sources"])).encode()).hexdigest()
    )
    dev, test = [], []
    for group in groups:
        (dev if len(dev) < max(1, len(items) * 0.3) else test).extend(group["items"])
    if not dev or not test:
        raise ValueError("共享出处使题目无法独立分组，请补充不相交的新题")
    manifest = {
        "origins": origins,
        "group_count": len(groups),
        "split_method": "共享标准出处的连通分组，按来源摘要排序，目标约 30% 开发题",
        "human_review_status": "pending",
        "prior_exposure": "原有题目已参与历史实验，当前划分不是全新未见测试集",
        "files": {},
    }
    for split, rows in [("development", dev), ("evaluation", test)]:
        data = {
            "origin": "既有模型生成题，经出处分组冻结；待人工复核",
            "split": split,
            "human_review_status": "pending",
            "prior_exposure": manifest["prior_exposure"],
            "items": rows,
        }
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
        name = split + ".json"
        (output / name).write_bytes(raw)
        manifest["files"][name] = {
            "questions": len(rows),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    manifest["shared_gold_sources"] = sorted(
        {g for item in dev for g in item["gold_doc_ids"]}
        & {g for item in test for g in item["gold_doc_ids"]}
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-map", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.datasets, args.output, args.source_map), ensure_ascii=False, indent=2
        )
    )
