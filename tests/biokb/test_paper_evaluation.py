"""论文级复算与原文档级评测口径隔离。"""

import json

import pytest

from biokb.errors import KnowledgeError
from biokb.evaluation import derive_paper_metrics, evaluate


def fixture_report():
    return {
        "k": 2,
        "dataset_sha256": "frozen-dataset",
        "corpus": {"sha256": "frozen-corpus"},
        "index_revision": 3,
        "results": {
            "bm25": {
                "Hit@2": 0.0,
                "Recall@2": 0.0,
                "AllHit@2": 0.0,
                "MRR@2": 0.0,
                "Precision@2": 0.0,
                "nDCG@2": 0.0,
                "details": [
                    {
                        "qid": "q1",
                        "gold": ["abstract-a"],
                        "ranked": ["fulltext-a", "other"],
                        "hit": False,
                        "evidence": [
                            {"source_id": "fulltext-a", "chunk_id": "a1"},
                            {"source_id": "fulltext-a", "chunk_id": "a2"},
                            {"source_id": "other", "chunk_id": "b1"},
                        ],
                    }
                ],
            }
        },
    }


def test_equivalent_abstract_and_fulltext_count_as_same_paper():
    original = fixture_report()
    report = derive_paper_metrics(
        original, {"abstract-a": "paper-a", "fulltext-a": "paper-a", "other": "paper-b"}
    )
    result = report["results"]["bm25"]
    assert result["Hit@2"] == 1
    assert result["AllHit@2"] == 1
    assert result["MRR@2"] == 1
    assert result["Precision@2"] == 0.5
    assert result["document_level_reference"]["Hit@2"] == 0
    assert result["details"][0]["ranked_papers"] == ["paper-a", "paper-b"]
    assert original["results"]["bm25"]["Hit@2"] == 0


def test_missing_gold_paper_mapping_fails_instead_of_dropping_gold():
    with pytest.raises(KnowledgeError, match="标准出处缺少论文映射"):
        derive_paper_metrics(fixture_report(), {"fulltext-a": "paper-a"})


def test_stale_corpus_report_is_rejected_before_paper_scoring(services, corpus, tmp_path):
    from scripts.derive_paper_metrics import run

    report = evaluate(
        services.retrieval,
        {"items": [{"qid": "q1", "question": "EGFR RAS", "gold_doc_ids": ["egfr"]}]},
        ["bm25"],
        2,
    )
    report["corpus"]["sha256"] = "outdated"
    source = tmp_path / "frozen.json"
    output = tmp_path / "paper.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="语料摘要不一致"):
        run(source, services.repository.path, output)
    assert not output.exists()
