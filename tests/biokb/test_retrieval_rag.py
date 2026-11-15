from dataclasses import replace

import pytest

from biokb.errors import IndexMismatchError, KnowledgeError, ProviderError
from biokb.providers import EmbeddingProvider
from biokb.rag import RAGService
from biokb.retrieval import RetrievalService


@pytest.mark.parametrize("strategy", ["bm25", "dense", "hybrid"])
def test_retrieval_returns_relevant_traceable_evidence(services, corpus, strategy):
    result = services.retrieval.search("mitochondrial ATP energy", strategy, 3)
    first = result["hits"][0]
    assert first["metadata"]["source_id"] == "mitochondria"
    doc = services.repository.document(first["document_id"])
    assert doc["text"][first["start_offset"] : first["end_offset"]] == first["text"]


def test_chinese_alias_expansion_retrieves_english_paper(services, corpus):
    assert (
        services.retrieval.search("线粒体", "bm25")["hits"][0]["metadata"]["source_id"]
        == "mitochondria"
    )


def test_no_evidence_does_not_generate_answer(services, corpus):
    result = services.rag.ask("quantum astronomical telescope", "hybrid")
    assert result["mode"] == "insufficient_evidence"
    assert not result["citations"]


def test_answer_history_keeps_evidence_after_document_removed(services, corpus):
    result = services.rag.ask("EGFR RAS", "bm25")
    assert result["mode"] == "extractive"
    source = result["citations"][0]
    services.repository.delete_document(source["document_id"])
    stored = services.repository.run(result["id"])
    assert stored["citations"][0]["text"] == source["text"]
    assert all(
        hit["document_id"] != source["document_id"]
        for hit in services.retrieval.search("EGFR RAS", "bm25")["hits"]
    )


def test_model_change_requires_reindex_but_bm25_still_works(services, corpus):
    settings = replace(services.settings, embedding_dimensions=256)
    retrieval = RetrievalService(services.repository, EmbeddingProvider(settings), settings)
    with pytest.raises(IndexMismatchError):
        retrieval.search("EGFR", "hybrid")
    assert retrieval.search("EGFR", "bm25")["hits"]


def test_failed_reindex_keeps_existing_vectors(services, corpus, monkeypatch):
    before = services.repository.index_rows()

    def fail(texts):
        raise ProviderError("failed")

    monkeypatch.setattr(services.ingestion.embedder, "embed_batch", fail)
    with pytest.raises(ProviderError):
        services.ingestion.reindex()
    assert services.repository.index_rows() == before


@pytest.mark.parametrize("value", [0, 51, True, 3.5, "5"])
def test_invalid_top_k_is_rejected(services, value):
    with pytest.raises(KnowledgeError):
        services.retrieval.search("EGFR", top_k=value)


def test_generated_answer_requires_valid_inline_citations(services, corpus):
    class Model:
        def answer(self, question, evidence):
            return {"answer": "EGFR activates RAS. [1]", "citations": [1]}

    settings = replace(services.settings, llm_backend="ollama", llm_model="test-model")
    rag = RAGService(services.repository, services.retrieval, Model(), settings)
    result = rag.ask("EGFR RAS", "bm25", 1)
    assert result["mode"] == "generated"
    assert result["model"] == "test-model"


@pytest.mark.parametrize(
    "output",
    [
        {"answer": "Unsupported. [99]", "citations": [99]},
        {"answer": "Uncited fact.", "citations": [1]},
        {"answer": "Fact. [1]", "citations": [True]},
    ],
)
def test_invalid_model_citation_falls_back_to_original_evidence(services, corpus, output):
    class Model:
        def answer(self, question, evidence):
            return output

    settings = replace(services.settings, llm_backend="ollama", llm_model="test-model")
    result = RAGService(services.repository, services.retrieval, Model(), settings).ask(
        "EGFR", "bm25", 1
    )
    assert result["mode"] == "provider_fallback"
    assert result["warnings"]
    assert "[99]" not in result["answer"]


def test_document_deletion_cascades_chunks_vectors(services, corpus):
    first = corpus[0]["document"]["id"]
    services.repository.delete_document(first)
    rows = services.repository.index_rows()
    assert all(row["document_id"] != first for row in rows)
    stats = services.repository.stats()
    assert stats["documents"] == 2
    assert stats["chunks"] == stats["vectors"]


def test_library_scope_evidence_selection_respects_count_and_document_cap(
    services, corpus, monkeypatch
):
    """固定候选顺序验证库级提问的证据截断；不调用真实生成模型。"""
    document_id = corpus[0]["document"]["id"]
    texts = [f"其他试验步骤 {index}：处理 48 小时。" for index in range(1, 5)]
    texts.append("脂肪变性诱导：油酸与棕榈酸 2:1，处理 24 小时，工作浓度 1 mM。")
    chunks = [
        {
            "chunk_id": f"doc_chain_{index}",
            "document_id": document_id,
            "paper_id": "",
            "title": "测试全文",
            "filename": "chain.txt",
            "text": text,
            "start_offset": index * 100,
            "end_offset": index * 100 + len(text),
            "locator": f"字符 {index * 100 + 1}–{index * 100 + len(text)}",
            "metadata": {"source_id": "chain"},
        }
        for index, text in enumerate(texts)
    ]

    def ranked_hits(query, strategy, top_k, **kwargs):
        return {
            "query": query,
            "strategy": strategy,
            "hits": chunks[:top_k],
            "warnings": [],
            "index_revision": services.repository.revision,
            "scope": {"kind": "library", "papers": [], "document_ids": [document_id]},
        }

    monkeypatch.setattr(services.retrieval, "search", ranked_hits)
    monkeypatch.setattr(services.repository, "evidence_window", lambda hit, neighbors: hit)
    target = chunks[-1]["chunk_id"]

    def cited(rag, count):
        return [row["chunk_id"] for row in rag.ask("脂肪变性诱导条件", "bm25", count)["citations"]]

    assert target not in cited(services.rag, 5)
    assert target not in cited(services.rag, 10)
    widened = RAGService(
        services.repository,
        services.retrieval,
        services.rag.model,
        replace(services.settings, max_evidence_per_document=5),
    )
    assert target in cited(widened, 10)
