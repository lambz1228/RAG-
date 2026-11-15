"""读取已填写盲评表，输出准确性、无支持断言与拒答指标；不代填评分。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.answer_evaluation import markdown_report, summarize_reviews  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize_reviews(args.run, args.reviews)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "answer_quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "回答质量评价.md").write_text(markdown_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "reviewed": report["reviewed_answers"],
                "planned": report["planned_answers"],
            },
            ensure_ascii=False,
        )
    )
