"""覆盖配置、模型协议和重建事务等真实边界回归。"""

import math
from dataclasses import replace

import pytest

from biokb.cli import main
from biokb.errors import ConflictError, KnowledgeError, ProviderError
from biokb.evaluation import evaluate
from biokb.providers import EmbeddingProvider, LanguageModel, normalize_vector
from biokb.rag import RAGService
from biokb.web.app import create_app


@pytest.mark.parametrize(
    "field,value",
    [("port", 0), ("port", 65536), ("workers", 0), ("top_k", True), ("max_context_chars", 0)],
)
def test_programmatic_config_is_validated(services, field, value):
    with pytest.raises(KnowledgeError):
        replace(services.settings, **{field: value}).validate()


def test_cli_rejects_port_zero_without_starting_server(monkeypatch, services):
    monkeypatch.setattr("biokb.cli.Settings.from_env", lambda: services.settings)
    assert main(["serve", "--port", "0"]) == 2


def test_large_finite_vector_is_normalized_without_overflow():
    vector = normalize_vector([1e308, 1e308])
    assert all(math.isfinite(value) for value in vector)
    assert sum(value * value for value in vector) == pytest.approx(1)


@pytest.mark.parametrize("index", [None, "0", True])
def test_malformed_vector_indices_raise_provider_error(services, monkeypatch, index):
    settings = replace(
        services.settings, embedding_backend="openai_compatible", embedding_model="test"
    )
    monkeypatch.setattr(
        "biokb.providers.post_json", lambda *args: {"data": [{"index": index, "embedding": [1, 0]}]}
    )
    with pytest.raises(ProviderError):
        EmbeddingProvider(settings).embed_batch(["document"])


@pytest.mark.parametrize("message", [None, [], "invalid"])
def test_malformed_ollama_message_becomes_explicit_fallback(services, corpus, monkeypatch, message):
    settings = replace(services.settings, llm_backend="ollama", llm_model="test")
    monkeypatch.setattr("biokb.providers.post_json", lambda *args: {"message": message})
    rag = RAGService(services.repository, services.retrieval, LanguageModel(settings), settings)
    assert rag.ask("EGFR RAS", "bm25")["mode"] == "provider_fallback"


@pytest.mark.parametrize(
    "payload",
    [{"query": "EGFR", "strategy": {"bad": "type"}}, {"query": "EGFR", "document_id": []}],
)
def test_wrong_parameter_types_return_400(services, payload):
    client = create_app(services.settings, services).test_client()
    assert client.post("/api/search", json=payload).status_code == 400


def test_unicode_access_token_does_not_cause_server_error(services):
    settings = replace(services.settings, api_token="中文访问令牌")
    client = create_app(settings, services).test_client()
    assert (
        client.get("/api/config", headers={"Authorization": "Bearer 中文访问令牌"}).status_code
        == 200
    )
    assert (
        client.get("/api/config", headers={"Authorization": "Bearer 错误令牌"}).status_code == 401
    )


def test_ingestion_job_does_not_repeat_entire_document(services):
    content = "用于入库的测试文献，包含足够的原文内容。" * 100
    created = services.ingestion.ingest("paper.txt", content.encode())
    repeated = services.ingestion.ingest("paper.txt", content.encode())
    assert "text" not in created["document"]
    assert "text" not in repeated["document"]
    assert services.repository.document(created["document"]["id"])["text"] == content


@pytest.mark.parametrize("wrong_count", [False, True])
def test_reindex_rejects_incomplete_or_cross_batch_dimension_changes(
    services, monkeypatch, wrong_count
):
    services.ingestion.ingest("long.txt", ("EGFR test sequence for chunking. " * 150).encode())
    before = services.repository.index_rows()
    assert len(before) > 16
    calls = 0

    def embed(texts):
        nonlocal calls
        calls += 1
        if wrong_count:
            return [[1, 0]] * (len(texts) - 1)
        return [[1, 0] if calls == 1 else [1, 0, 0] for text in texts]

    monkeypatch.setattr(services.ingestion.embedder, "embed_batch", embed)
    with pytest.raises(ProviderError):
        services.ingestion.reindex()
    assert services.repository.index_rows() == before


def test_context_budget_keeps_whole_traceable_chunks(services):
    services.ingestion.ingest("long.txt", ("EGFR test sequence for context. " * 100).encode())
    captured = []

    class Model:
        def answer(self, question, evidence):
            captured.extend(evidence)
            return {"answer": "测试回答 [1]", "citations": [1]}

    settings = replace(
        services.settings, max_context_chars=1000, llm_backend="ollama", llm_model="test"
    )
    result = RAGService(services.repository, services.retrieval, Model(), settings).ask(
        "EGFR", "bm25", 20
    )
    assert sum(len(hit["text"]) for hit in captured) <= 1000
    assert result["context"]["used_chunks"] < result["context"]["retrieved_chunks"]
    for hit in captured:
        text = services.repository.document(hit["document_id"])["text"]
        assert text[hit["start_offset"] : hit["end_offset"]] == hit["text"]


def test_evaluation_rejects_mixed_index_revisions(services, corpus, monkeypatch):
    search = services.retrieval.search

    def changing_search(*args, **kwargs):
        result = search(*args, **kwargs)
        services.repository.delete_document(corpus[0]["document"]["id"])
        return result

    monkeypatch.setattr(services.retrieval, "search", changing_search)
    with pytest.raises(ConflictError):
        evaluate(
            services.retrieval, {"items": [{"question": "EGFR", "gold_doc_id": "egfr"}]}, ["bm25"]
        )


def test_evaluation_requires_gold_source_coverage(services, corpus):
    with pytest.raises(KnowledgeError, match="标准出处不在知识库"):
        evaluate(
            services.retrieval,
            {"items": [{"question": "EGFR", "gold_doc_id": "missing"}]},
            ["bm25"],
        )


def test_explicit_legacy_mapping_preserves_original_gold(services, corpus):
    dataset = {"items": [{"question": "EGFR RAS", "gold_doc_id": "old-egfr"}]}
    report = evaluate(services.retrieval, dataset, ["bm25"], source_map={"old-egfr": "egfr"})
    assert report["results"]["bm25"]["Hit@10"] == 1
    detail = report["results"]["bm25"]["details"][0]
    assert detail["gold"] == ["egfr"] and detail["original_gold"] == ["old-egfr"]
    assert dataset["items"][0]["gold_doc_id"] == "old-egfr"


def test_source_mapping_cannot_redirect_existing_gold(services, corpus):
    with pytest.raises(KnowledgeError):
        evaluate(
            services.retrieval,
            {"items": [{"question": "EGFR", "gold_doc_id": "egfr"}]},
            ["bm25"],
            source_map={"egfr": "autophagy"},
        )
