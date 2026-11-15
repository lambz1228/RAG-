"""在源库只读副本上运行问答对照，输出匿名盲评表；不计算学术正确率。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.experiments import run_experiment  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="仅运行哈希检索和原文摘录流程演示")
    args = parser.parse_args()
    settings = Settings() if args.offline else Settings.from_env()
    summary = run_experiment(args.database, args.dataset, args.output, settings, args.offline)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(bool(summary["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
