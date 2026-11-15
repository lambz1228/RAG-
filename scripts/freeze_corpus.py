"""冻结或只读校验语料库；每个版本使用新的目录。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.corpus import freeze_corpus, verify_corpus  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not args.verify and not args.source:
        parser.error("冻结时需要 --source")
    report = verify_corpus(args.output) if args.verify else freeze_corpus(args.source, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.get("passed", True) else 1)
