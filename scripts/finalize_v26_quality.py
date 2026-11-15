"""汇总 v26 机器审计、实验与来源登记，保留逐题及原始响应。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

METHODS = ("bm25", "dense", "hybrid", "hybrid_rerank")


def load(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve(strict=True)
    experiment = root / "experiment_01"
    local = load(root / "local_audit.json")
    official = load(root / "official_audit.json")
    access = load(root / "abstract_access_inventory.json")
    staged = load(root / "abstract_fulltext_staging" / "staging_inventory.json")
    staging_summary = load(root / "abstract_fulltext_staging" / "staging_summary.json")
    qa = load(experiment / "qa_machine_diagnostic.json")
    cases = load(experiment / "fragment_cases.json")["cases"]
    source_questions = load(Path("output/upgrade_20261104/answer_review_final/fulltext_questions.json"))
    by_qid = {row["qid"]: row for row in source_questions["items"]}
    qa_by_qid = {row["qid"]: row for row in qa["results"]}

    snapshot = experiment / "snapshot" / "knowledge.sqlite3"
    frozen_at = datetime.fromisoformat(load(experiment / "protocol.json")["frozen_at_utc"])
    with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as connection:
        raw_runs = [json.loads(row[1]) for row in connection.execute(
            "SELECT created_at,result FROM query_runs WHERE kind='answer' ORDER BY created_at,id"
        ) if datetime.fromisoformat(row[0].replace("Z", "+00:00")) >= frozen_at]
    if len(raw_runs) != len(cases):
        raise ValueError("实验副本问答记录数与开发题不一致")
    save(experiment / "qa_frozen_snapshots.json", raw_runs)

    methods = {}
    for method in METHODS:
        document = load(experiment / f"legacy_{method}.json")["results"][method]
        paper = load(experiment / f"legacy_paper_{method}.json")["results"][method]
        fragment = load(experiment / f"fragments_{method}.json")
        methods[method] = {
            "n_legacy_model_questions": document["n"],
            "document_Hit@10": document["Hit@10"],
            "document_Recall@10": document["Recall@10"],
            "document_MRR@10": document["MRR@10"],
            "paper_Hit@10": paper["Hit@10"],
            "paper_Recall@10": paper["Recall@10"],
            "fragment_developer_cases": fragment["n_scored"],
            "fragment_any_overlap@5": fragment["any_overlap@5"],
            "fragment_any_overlap@10": fragment["any_overlap@10"],
            "fragment_coverage_95@5": fragment["coverage_95@5"],
            "fragment_coverage_95@10": fragment["coverage_95@10"],
        }

    literal_findings = {
        "fulltext-03": {
            "source_quote_mentions_1640": "1640" in by_qid["fulltext-03"]["evidence_quote"],
            "model_answer_mentions_1640_or_rpmi": any(
                term in qa_by_qid["fulltext-03"]["answer"].casefold()
                for term in ("1640", "rpmi")
            ),
            "human_correctness": None,
        },
        "fulltext-04": {
            "source_quote_mentions_conditions": all(
                term in by_qid["fulltext-04"]["evidence_quote"]
                for term in ("2:1", "24 h", "1 mM")
            ),
            "model_answer_mode": qa_by_qid["fulltext-04"]["mode"],
            "human_correctness": None,
        },
    }
    relation_types = Counter(row["type"] for row in official["relations"])
    title_anomalies = [row["pmid"] for row in official["comparisons"]
                       if not row["title_matches"]]
    journal_year_differences = [row["pmid"] for row in official["comparisons"]
                                if not row["year_matches"]]
    access_by_pmid = {row["pmid"]: row for row in access}
    staged_by_pmid = {row["pmid"]: row for row in staged}
    csv_path = root / "abstract_acquisition_register.csv"
    fields = ["PMID", "题名", "PMCID", "摘要字符数", "OA标记", "全文索引标记",
              "XML路径", "XML_SHA256", "许可机器分类", "下一版候选", "当前状态", "人工复核"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for entry in official["abstract_only"]:
            access_row = access_by_pmid[entry["pmid"]]
            staged_row = staged_by_pmid[entry["pmid"]]
            writer.writerow({
                "PMID": entry["pmid"], "题名": entry["title"],
                "PMCID": access_row["europepmc_pmcid"],
                "摘要字符数": entry["abstract_chars"],
                "OA标记": access_row["europepmc_open_access"],
                "全文索引标记": access_row["europepmc_fulltext_indexed"],
                "XML路径": staged_row.get("xml_path", ""),
                "XML_SHA256": staged_row.get("xml_sha256", ""),
                "许可机器分类": staged_row.get("license_class", ""),
                "下一版候选": "是" if staged_row.get("candidate_for_next_batch") else "否",
                "当前状态": staged_row["state"], "人工复核": "",
            })
    summary = {
        "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "v26 正式库机器审计与独立副本上的开发诊断；科研质量未放行",
        "formal_database_sha256": local["database_sha256_before"],
        "formal_database_unchanged": load(experiment / "preservation_after.json")["formal_database_unchanged"],
        "index_revision": local["index_revision"],
        "paper_summary": local["paper_summary"],
        "fulltext_raw_chain_passed": sum(row["passed"] for row in local["fulltext_checks"]),
        "fulltext_raw_chain_total": len(local["fulltext_checks"]),
        "chunk_text_errors": len(local["chunk_text_errors"]),
        "vector_profiles": local["vector_profiles"],
        "official_pmids_checked": len(official["returned_pmids"]),
        "official_pmids_missing": official["missing_pmids"],
        "pmcid_mismatches": [row["pmid"] for row in official["comparisons"]
                             if not row["pmcid_matches"]],
        "doi_mismatches": [row["pmid"] for row in official["comparisons"]
                           if not row["doi_matches"]],
        "title_anomaly_pmids": title_anomalies,
        "journal_year_difference_pmids": journal_year_differences,
        "all_years_match_either_journal_or_online": all(
            row["year_matches_any_publication_date"] for row in official["comparisons"]
        ),
        "relation_types": dict(relation_types),
        "relation_target_pubmed_checked": sum(row["target_found"] for row in official["relations"]),
        "relation_doi_only_unresolved": sum(
            not row["target_found"] and bool(row["target_doi_in_citation"])
            for row in official["relations"]
        ),
        "abstract_access": {
            "total": len(access), "europepmc_found": sum(row["europepmc_found"] for row in access),
            "open_access_flag": staging_summary["oa_flag"],
            "xml_saved": staging_summary["xml_saved"],
            "cc_by_or_cc0_uri_candidate": staging_summary["cc_by_or_cc0_uri_candidate"],
            "not_open_access_flagged": sum(row["europepmc_open_access"] != "Y" for row in access),
        },
        "methods": methods,
        "qa_machine": {
            "n": qa["n"], "generated_or_partial": qa["generated_or_partial"],
            "citation_offsets_exact": qa["citation_offsets_exact"],
            "modes": dict(Counter(row.get("mode", "error") for row in qa["results"])),
            "literal_findings": literal_findings,
        },
        "independent_expert_questions": None,
        "domain_reviewer_scores": None,
        "academic_correctness_rate": None,
        "release_decision": "科研质量未放行",
    }
    save(root / "machine_results_summary.json", summary)
    manifest = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "OUTPUT_MANIFEST.json":
            manifest[str(path.relative_to(root)).replace("\\", "/")] = {
                "bytes": path.stat().st_size, "sha256": sha256_file(path)
            }
    save(root / "OUTPUT_MANIFEST.json", {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                                         "files": manifest})
    print(json.dumps({
        "papers": summary["paper_summary"]["papers"],
        "fulltext_chain": summary["fulltext_raw_chain_passed"],
        "relation_target_checked": summary["relation_target_pubmed_checked"],
        "xml_saved": summary["abstract_access"]["xml_saved"],
        "manifest_files": len(manifest),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
