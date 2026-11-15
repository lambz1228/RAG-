"""任务书语料冻结、评分完整性与答卷绑定；评分数据仅为受控测试夹具。"""

import csv
import json

import pytest

from biokb.answer_evaluation import REQUIRED, summarize_reviews
from biokb.corpus import freeze_corpus, sha256, verify_corpus
from biokb.errors import KnowledgeError
from biokb.experiments import export_review


def test_corpus_freeze_preserves_source_and_detects_changed_files(services, corpus, tmp_path):
    original = services.settings.database_path
    before = sha256(original)
    target = tmp_path.parent / (tmp_path.name + "-frozen")
    manifest = freeze_corpus(services.settings.data_dir, target)
    assert len(manifest["documents"]) == 3
    assert sha256(original) == before
    assert verify_corpus(target)["passed"]
    archived = next((target / "uploads").iterdir())
    archived.write_bytes(archived.read_bytes() + b"changed")
    assert not verify_corpus(target)["passed"]
    with pytest.raises(KnowledgeError):
        freeze_corpus(services.settings.data_dir, target)


@pytest.fixture
def review_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "protocol.json").write_text(
        json.dumps(
            {
                "scope": "受控单元测试夹具，不是人工评价",
                "question_ids": ["q1", "q2"],
                "dataset": {"sha256": "fixture"},
                "arms": [
                    {"name": "no_rag", "strategy": "none"},
                    {"name": "rag", "strategy": "dense"},
                ],
            }
        )
    )
    questions = [
        {"qid": "q1", "question": "有依据的问题", "reference": {"answerable": True}},
        {"qid": "q2", "question": "无依据的问题", "reference": {"answerable": False}},
    ]
    results = [
        {
            "qid": q["qid"],
            "arm": arm,
            "answer": "受控测试答案",
            "mode": "generated",
            "citations": [],
        }
        for q in questions
        for arm in ("no_rag", "rag")
    ]
    (run / "results.jsonl").write_text("\n".join(json.dumps(r) for r in results))
    export_review(run, questions, results, 12)
    return run


def fill_review(run, update):
    path = run / "盲评表.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    update(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def complete_rows(rows):
    for row in rows:
        row.update({field: "2" for field in REQUIRED[:3]})
        row.update(
            {
                "拒答恰当性(恰当/漏答/误答)": "恰当",
                "无支持断言数": "0",
                "评分者": "单元测试虚构评分者",
                "评分时间": "2026-11-15",
            }
        )


def test_blank_reviews_never_become_zero_or_hundred_percent(review_run):
    report = summarize_reviews(review_run, review_run / "盲评表.csv")
    assert not report["complete"] and report["reviewed_answers"] == 0
    assert all(row["metrics"] is None for row in report["arms"].values())


def test_complete_controlled_reviews_have_distinct_denominators(review_run):
    path = fill_review(review_run, complete_rows)
    report = summarize_reviews(review_run, path)
    assert report["complete"] and report["reviewed_answers"] == 4
    assert report["arms"]["no_rag"]["metrics"]["citation_support_rate"] is None
    assert report["arms"]["rag"]["metrics"]["fact_fully_correct_rate"] == 1
    assert report["arms"]["rag"]["metrics"]["negative_question_count"] == 1
    assert not report["reviewer_identity_verified"]


def test_partial_or_invalid_rating_is_rejected(review_run):
    path = fill_review(review_run, lambda rows: rows[0].update({"评分者": "测试"}))
    with pytest.raises(KnowledgeError, match="未填写"):
        summarize_reviews(review_run, path)


def test_fallback_is_visible_and_cannot_be_scored_as_model_success(review_run):
    bundle = json.loads((review_run / "评分运行包_协调者保管.json").read_text())
    results = bundle["results"]
    results[1]["mode"] = "provider_fallback"
    questions = [
        {"qid": "q1", "question": "有依据的问题", "reference": {"answerable": True}},
        {"qid": "q2", "question": "无依据的问题", "reference": {"answerable": False}},
    ]
    (review_run / "results.jsonl").write_text("\n".join(json.dumps(r) for r in results))
    export_review(review_run, questions, results, 12)
    assert "模型回退" in (review_run / "盲评表.csv").read_text(encoding="utf-8-sig")
    report = summarize_reviews(review_run, review_run / "盲评表.csv")
    assert report["arms"]["rag"]["status"] == "run_failed"
    assert report["arms"]["rag"]["metrics"] is None
    path = fill_review(review_run, complete_rows)
    with pytest.raises(KnowledgeError, match="模型回退不能计为有效"):
        summarize_reviews(review_run, path)


def test_review_from_another_run_or_modified_output_is_rejected(review_run):
    path = fill_review(review_run, lambda rows: rows[0].update({"答卷摘要": "wrong-run"}))
    with pytest.raises(KnowledgeError, match="不属于"):
        summarize_reviews(review_run, path)
    result = review_run / "results.jsonl"
    result.write_text(result.read_text().replace("generated", "generated") + "\n")
    (review_run / "protocol.json").write_text((review_run / "protocol.json").read_text() + "\n")
    with pytest.raises(KnowledgeError, match="协议已变化"):
        summarize_reviews(review_run, path)


def test_review_bundle_and_web_upload_preserve_pending_status(review_run, services):
    import io

    from biokb.answer_evaluation import summarize_bundle
    from biokb.web.app import create_app

    bundle = json.loads((review_run / "评分运行包_协调者保管.json").read_text())
    reviews = (review_run / "盲评表.csv").read_bytes()
    assert summarize_bundle(bundle, reviews)["reviewed_answers"] == 0
    client = create_app(services.settings, services).test_client()
    response = client.post(
        "/api/answer-evaluations",
        data={
            "bundle": (io.BytesIO(json.dumps(bundle).encode()), "bundle.json"),
            "reviews": (io.BytesIO(reviews), "reviews.csv"),
        },
    )
    assert response.status_code == 200
    report = response.json["report"]
    assert not report["complete"]
    assert client.get("/api/history/" + report["id"]).json["result"]["reviewed_answers"] == 0
    assert client.post("/api/answer-evaluations", data={}).status_code == 400


@pytest.mark.parametrize("citations", [[2], [True], [], [0]])
def test_structured_claims_still_reject_invalid_evidence_numbers(citations):
    from biokb.errors import ProviderError
    from biokb.providers import LanguageModel

    with pytest.raises(ProviderError):
        LanguageModel.render_claims(
            {
                "status": "answered",
                "claims": [{"text": "测试论断", "citations": citations}],
                "explanation": "",
            },
            1,
        )


def test_structured_provider_keeps_evidence_and_preserves_strict_rag_contract(
    services, monkeypatch
):
    from dataclasses import replace

    from biokb.providers import LanguageModel
    from biokb.rag import RAGService

    captured = []

    def post(url, payload, key, timeout):
        captured.append(payload)
        response = {
            "status": "answered",
            "claims": [{"text": "受控测试论断", "citations": [1]}],
            "explanation": "",
        }
        return {"choices": [{"message": {"content": json.dumps(response)}}]}

    monkeypatch.setattr("biokb.providers.post_json", post)
    settings = replace(
        services.settings,
        llm_backend="openai_compatible",
        llm_model="test",
        llm_response_format="json_schema",
        llm_output_mode="claims",
    )
    model = LanguageModel(settings)
    response = model.answer("测试问题", [{"title": "测试来源", "text": "受控原文"}])
    assert json.loads(captured[0]["messages"][1]["content"])["evidence"][0]["text"] == "受控原文"
    assert captured[0]["response_format"]["type"] == "json_schema"
    assert RAGService._validate_answer(response, 1) == ("受控测试论断 [1]", [1], "answered")
    assert response["citation_rendering"] == "structured-claims-v1"
    assert len(response["raw_response_sha256"]) == 64


def test_claims_cannot_hide_uncited_factual_text_in_insufficient_answer():
    from biokb.providers import LanguageModel

    response = LanguageModel.render_claims(
        {"status": "insufficient", "claims": [], "explanation": "这里可能有未经支持的生物事实"}, 1
    )
    assert "未经支持的生物事实" not in response["answer"]
    assert response["citations"] == []


def test_partial_run_cannot_be_mistaken_for_complete_review(review_run):
    from biokb.answer_evaluation import summarize_bundle

    bundle = json.loads((review_run / "评分运行包_协调者保管.json").read_text())
    bundle["results"] = bundle["results"][:-1]
    bundle["keys"] = bundle["keys"][:-1]
    with pytest.raises(KnowledgeError, match="不完整"):
        summarize_bundle(bundle, (review_run / "盲评表.csv").read_bytes())
