"""在 SQLite 临时副本上核对扩充版论文目录与检索 API。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from biokb.config import Settings
from biokb.web.app import create_app
from scripts.expand_open_corpus import file_sha256, save_json_atomic, utc_now


def verify(batch: Path, target: Path) -> dict:
    batch = batch.resolve(strict=True)
    target = target.resolve(strict=True)
    acquisition = json.loads((batch / "acquisition_report.json").read_text(encoding="utf-8"))
    index = json.loads((batch / "index_verification.json").read_text(encoding="utf-8"))
    if not index["passed"]:
        raise ValueError("索引核验未通过，不能执行 API 验收")
    source = target / "knowledge.sqlite3"
    before = file_sha256(source)
    errors = []
    checked_papers = 0
    title_queries = []
    summary = {}
    with tempfile.TemporaryDirectory(prefix="biokb-expanded-api-") as temporary:
        copy_path = Path(temporary) / "knowledge.sqlite3"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(copy_path)) as copied:
                original.backup(copied)
        settings = replace(Settings.from_env(), data_dir=Path(temporary), api_token="")
        application = create_app(settings)
        try:
            with application.test_client() as client:
                health = client.get("/api/health")
                if health.status_code != 200 or health.get_json().get("status") != "ok":
                    errors.append("健康接口异常")
                catalog = client.get("/api/papers?limit=1")
                if catalog.status_code != 200:
                    errors.append("论文目录接口异常")
                else:
                    summary = catalog.get_json()["summary"]
                    expected = index["counts"]
                    if (
                        summary["papers"] != expected["target_unique_papers"]
                        or summary["records"] != expected["target_documents"]
                        or summary["fulltext_papers"]
                        != expected["content_types"]["fulltext"]
                        or summary["identity_conflicts"] != 0
                    ):
                        errors.append("论文目录与索引核验数量不一致")
                for item in acquisition["items"]:
                    response = client.get("/api/papers", query_string={"q": item["pmcid"]})
                    data = response.get_json() or {}
                    matches = data.get("items", [])
                    if response.status_code != 200 or len(matches) != 1:
                        errors.append(f"论文目录无法唯一定位 {item['pmcid']}")
                        continue
                    paper = matches[0]
                    if (
                        paper["identifiers"].get("pmid") != item["pmid"]
                        or paper["identifiers"].get("doi", "").lower()
                        != item["doi"].lower()
                        or paper["year"] != item["year"]
                        or item["topic"] not in paper["topics"]
                        or not paper["has_fulltext"]
                        or paper["identity_conflict"]
                    ):
                        errors.append(f"论文目录字段不一致：{item['pmcid']}")
                    checked_papers += 1
                for item in (acquisition["items"][0], acquisition["items"][-1]):
                    response = client.post(
                        "/api/search",
                        json={
                            "query": item["title"][:400],
                            "strategy": "bm25",
                            "top_k": 10,
                            "filters": {"year": item["year"], "topic": item["topic"]},
                        },
                    )
                    hits = (response.get_json() or {}).get("hits", [])
                    found = any(
                        hit["metadata"].get("pmcid") == item["pmcid"] for hit in hits
                    )
                    title_queries.append({"pmcid": item["pmcid"], "found": found})
                    if response.status_code != 200 or not found:
                        errors.append(f"题名检索功能冒烟失败：{item['pmcid']}")
        finally:
            application.extensions["biokb"].jobs.close()
    after = file_sha256(source)
    if before != after or before != index["target_database_sha256"]:
        errors.append("正式库摘要与索引报告不符或验收期间发生变化")
    result = {
        "verified_at_utc": utc_now(),
        "target_database_sha256": after,
        "read_only_copy": True,
        "summary": summary,
        "checked_papers": checked_papers,
        "title_queries": title_queries,
        "errors": errors,
        "passed": not errors,
        "research_accuracy": "未评估；题名查询仅为功能冒烟测试",
    }
    save_json_atomic(batch / "api_verification.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.batch, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
