"""只读核验 v26 冻结产物、正式数据库及原始来源文件的摘要。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(output: Path) -> list[str]:
    manifest = json.loads((output / "OUTPUT_MANIFEST.json").read_text(encoding="utf-8"))
    expected = manifest["files"]
    actual = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "OUTPUT_MANIFEST.json"
    }
    problems = [f"清单缺少文件：{name}" for name in sorted(actual - expected.keys())]
    problems.extend(f"文件不存在：{name}" for name in sorted(expected.keys() - actual))
    for name in sorted(actual & expected.keys()):
        path = output / name
        item = expected[name]
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            problems.append(f"摘要或大小不一致：{name}")
    return problems


def verify_sources(local_audit: dict, project_root: Path) -> list[str]:
    problems = []
    for raw_name, expected_sha256 in local_audit["source_files_sha256"].items():
        source = PureWindowsPath(raw_name)
        if source.is_absolute() or ".." in source.parts:
            problems.append(f"来源路径非法：{raw_name}")
            continue
        path = project_root.joinpath(*source.parts)
        if not path.is_file():
            problems.append(f"来源文件不存在：{raw_name}")
        elif sha256_file(path) != expected_sha256:
            problems.append(f"来源摘要不一致：{raw_name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve(strict=True)
    database = args.database.resolve(strict=True)
    local_audit = json.loads((output / "local_audit.json").read_text(encoding="utf-8"))
    protocol = json.loads((output / "experiment_01" / "protocol.json").read_text(
        encoding="utf-8"
    ))
    summary = json.loads((output / "machine_results_summary.json").read_text(
        encoding="utf-8"
    ))

    problems = verify_manifest(output)
    problems.extend(verify_sources(local_audit, ROOT))
    database_sha256 = sha256_file(database)
    expected_sha256 = local_audit["database_sha256_before"]
    if database_sha256 != expected_sha256:
        problems.append("正式数据库摘要与审计冻结值不一致")
    if protocol["database_sha256"] != expected_sha256:
        problems.append("实验协议数据库摘要与审计冻结值不一致")
    if summary["formal_database_sha256"] != expected_sha256:
        problems.append("汇总数据库摘要与审计冻结值不一致")
    if str(protocol["index_revision"]) != str(local_audit["index_revision"]):
        problems.append("实验协议索引修订号与审计冻结值不一致")
    if any(summary[field] is not None for field in (
        "independent_expert_questions", "domain_reviewer_scores", "academic_correctness_rate"
    )):
        problems.append("人工评审或学术正确率字段不应由机器产物填写")

    result = {
        "manifest_files": len(json.loads((output / "OUTPUT_MANIFEST.json").read_text(
            encoding="utf-8"
        ))["files"]),
        "source_files": len(local_audit["source_files_sha256"]),
        "formal_database_sha256": database_sha256,
        "problems": problems,
        "passed": not problems,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
