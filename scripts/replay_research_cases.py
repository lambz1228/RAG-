"""在正式库的独立副本复测既有失败题，保留完整证据和运行配置。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.papers import list_papers  # noqa: E402

SOURCE = ROOT / "data/biokb-v21-semantic/knowledge.sqlite3"
ARCHIVE = ROOT / "output/upgrade_20261104/answer_review_release/answers.json"


def run(case_id: str, output: Path) -> dict:
    cases = json.loads(ARCHIVE.read_text(encoding="utf-8"))["items"]
    original = next((item for item in cases if item["qid"] == case_id), None)
    if original is None or case_id not in {"fulltext-03", "fulltext-04"}:
        raise ValueError("只允许复测已记录的 fulltext-03 / fulltext-04")
    source_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="biokb-replay-") as temporary:
        working = Path(temporary)
        with closing(sqlite3.connect(SOURCE.resolve().as_uri() + "?mode=ro", uri=True)) as existing:
            with closing(sqlite3.connect(working / "knowledge.sqlite3")) as copied:
                existing.backup(copied)
        settings = replace(Settings.from_env(), data_dir=working)
        services = build_services(settings)
        try:
            found = list_papers(services.repository, query="PEX13")
            matches = [paper for paper in found["items"] if paper["has_fulltext"]]
            if len(matches) != 1:
                raise AssertionError("复测范围无法唯一对应全文论文")
            paper_id = matches[0]["id"]
            answer = services.rag.ask(
                original["question"], top_k=10, paper_ids=[paper_id], context_neighbors=1
            )
            report = {
                "case_id": case_id,
                "original_question": original["question"],
                "original_answer": original["result"]["answer"],
                "original_mode": original["result"]["mode"],
                "current": answer,
                "paper_id": paper_id,
                "source_sha256": source_sha,
                "source_unchanged": hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_sha,
                "configuration": settings.public(),
                "human_review_status": "pending",
            }
        finally:
            services.jobs.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "case_id": case_id,
        "mode": report["current"]["mode"],
        "answer": report["current"]["answer"],
        "evidence_chunks": len(report["current"]["citations"]),
        "source_unchanged": report["source_unchanged"],
        "output": str(output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", choices=["fulltext-03", "fulltext-04"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.case_id, args.output), ensure_ascii=False, indent=2))
