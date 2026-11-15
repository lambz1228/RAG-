"""在源库只读副本上运行问答对照，输出匿名盲评表；不计算学术正确率。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings, load_env  # noqa: E402
from biokb.experiments import run_experiment  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="仅运行哈希检索和原文摘录流程演示")
    parser.add_argument("--profile", choices=["taskbook", "core", "rerank"], default="taskbook")
    parser.add_argument("--env-file", type=Path, help="显式读取模型配置，不覆盖已有环境变量")
    args = parser.parse_args()
    if args.env_file:
        load_env(args.env_file.resolve(strict=True))
    settings = Settings() if args.offline else Settings.from_env()
    summary = run_experiment(
        args.database, args.dataset, args.output, settings, args.offline, profile=args.profile
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(bool(summary["errors"] or summary["provider_fallbacks"]))


if __name__ == "__main__":
    raise SystemExit(main())
