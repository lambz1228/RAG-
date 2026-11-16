"""实验隔离、标准答案不泄漏、证据区间覆盖和冻结题库完整性回归。"""

import csv
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from biokb.errors import KnowledgeError
from biokb.experiments import evidence_coverage, file_sha256, load_questions, run_experiment
from biokb.providers import LanguageModel
from scripts.audit_questionbank import audit
from scripts.questionbank import freeze

ROOT = Path(__file__).resolve().parents[2]


def test_interval_coverage_uses_union_and_correct_document():
    gold = [{"document_id": "a", "start_offset": 10, "end_offset": 30}]
    hits = [
        {"document_id": doc, "start_offset": a, "end_offset": b}
        for doc, a, b in [("a", 5, 20), ("a", 15, 25), ("b", 0, 100)]
    ]
    assert evidence_coverage(gold, hits) == 0.75
    assert evidence_coverage([], hits) is None


def test_no_rag_payload_has_only_question_and_separate_prompt(services, monkeypatch):
    captured = []

    def post(url, payload, key, timeout):
        captured.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"answer":"不知道","status":"insufficient","citations":[]}'
                    }
                }
            ]
        }

    monkeypatch.setattr("biokb.providers.post_json", post)
    model = LanguageModel(
        replace(services.settings, llm_backend="openai_compatible", llm_model="test")
    )
    assert model.answer_without_retrieval("测试问题")["citations"] == []
    request = captured[0]
    assert json.loads(request["messages"][1]["content"]) == {"question": "测试问题"}
    assert "已有知识" in request["messages"][0]["content"]


def test_offline_experiment_preserves_source_and_blinds_arms(services, corpus, tmp_path):
    dataset = tmp_path / "dataset.json"
    doc = services.repository.document(corpus[0]["document"]["id"])
    secret = "标准答案绝不能交给生成模型"
    dataset.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "qid": "q1",
                        "question": "EGFR RAS",
                        "reference_answer": secret,
                        "evidence": [
                            {"document_id": doc["id"], "start_offset": 0, "end_offset": 20}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    before = file_sha256(services.settings.database_path)
    output = tmp_path / "experiment"
    summary = run_experiment(
        services.settings.database_path, dataset, output, services.settings, offline=True
    )
    assert summary["errors"] == 0 and summary["completed_records"] == 3
    assert summary["source_unchanged"] and summary["academic_correctness_rate"] is None
    protocol = json.loads((output / "protocol.json").read_text())
    assert protocol["human_review_required"] is False
    assert file_sha256(services.settings.database_path) == before
    with (output / "盲评表.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert all("arm" not in row and not row["评分者"] for row in rows)
    assert secret not in (output / "results.jsonl").read_text()
    assert secret in (output / "协调者密钥_勿分发给盲评者.json").read_text()
    with pytest.raises(FileExistsError):
        run_experiment(
            services.settings.database_path, dataset, output, services.settings, offline=True
        )


def test_real_experiment_refuses_hash_and_does_not_create_output(services, corpus, tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"items": [{"qid": "a", "question": "EGFR"}]}))
    output = tmp_path / "experiment"
    with pytest.raises(KnowledgeError, match="真实模型实验"):
        run_experiment(services.settings.database_path, dataset, output, services.settings)
    assert not output.exists()


def test_frozen_banks_preserve_history_without_human_review_gate():
    bank = ROOT / "questionbank/v1-machine"
    report, rows = audit(bank, ROOT / "questionbank/v0-dev")
    assert report["integrity_passed"] and len(rows) == 82
    assert report["missing_total"] == 28 and report["target_question_count"] == 110
    assert not report["formal_ready"] and report["human_review_records"] == 0
    assert report["human_review_required"] is False
    assert len(load_questions(bank)[0]) == 82
    assert len(load_questions(ROOT / "questionbank/v0-dev")[0]) == 6


def test_new_freeze_writes_platform_independent_bytes(tmp_path):
    database = tmp_path / "fake.sqlite3"
    database.write_bytes(b"database-fixture")
    rows = [
        {
            "题号": "one",
            "题型": "无答案",
            "研究问题": "test",
            "无答案或冲突说明": "fixture",
            "开发或正式集": "正式集",
        }
    ]
    manifest = freeze(rows, {}, database, "test", tmp_path / "bank")
    raw = (tmp_path / "bank/test/questions.jsonl").read_bytes()
    assert b"\r" not in raw
    assert hashlib.sha256(raw).hexdigest() == manifest["questions_file_sha256"]


def test_all_real_model_arms_use_same_frozen_questions_without_gold(
    services, corpus, tmp_path, monkeypatch
):
    from biokb.container import build_services

    captured = []

    class Model:
        fingerprint = "controlled-test-model"

        def answer(self, question, evidence):
            captured.append((question, evidence))
            return {"answer": "测试输出[1]", "citations": [1], "status": "answered"}

        def answer_without_retrieval(self, question):
            captured.append((question, []))
            return {"answer": "测试输出", "citations": [], "status": "answered"}

    class Reranker:
        fingerprint = "controlled-test-reranker"

        def rank(self, question, hits):
            return hits

    def controlled_builder(settings):
        built = build_services(settings, embedder=services.retrieval.embedder, model=Model())
        built.retrieval.reranker = Reranker()
        return built

    monkeypatch.setattr("biokb.experiments.build_services", controlled_builder)
    settings = replace(
        services.settings,
        embedding_backend="openai_compatible",
        embedding_model="controlled",
        llm_backend="openai_compatible",
        llm_model="controlled",
        reranker_backend="compatible",
        reranker_model="controlled",
    )
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            {"items": [{"qid": "q1", "question": "EGFR RAS", "reference_answer": "FORBIDDEN-GOLD"}]}
        )
    )
    output = tmp_path / "real-controlled"
    summary = run_experiment(
        services.settings.database_path, dataset, output, settings, profile="rerank"
    )
    assert summary["errors"] == 0 and summary["completed_records"] == 7
    assert len(captured) == 7
    assert all(question == "EGFR RAS" for question, _ in captured)
    assert "FORBIDDEN-GOLD" not in json.dumps(captured)
    assert sum(not evidence for _, evidence in captured) == 1
    assert summary["academic_correctness_rate"] is None
