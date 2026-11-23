"""双路查询保留原问题、权限范围、基线与失败回退，缓存不依赖真实接口。"""

from dataclasses import replace

import pytest

from biokb.errors import ProviderError
from biokb.query_translation import QueryTranslator
from biokb.retrieval import RetrievalService


def settings(services):
    return replace(
        services.settings,
        query_translation=True,
        llm_backend="openai_compatible",
        llm_model="fixture",
    )


def test_dual_query_retrieves_english_and_preserves_original(services, corpus, monkeypatch):
    retrieval = RetrievalService(
        services.repository, services.retrieval.embedder, settings(services)
    )
    calls = []

    def translate(query):
        calls.append(query)
        return {"english_query": "mitochondria ATP", "cache_hit": False}

    monkeypatch.setattr(retrieval.translator, "translate", translate)
    query = "胞质中的能量装置"
    result = retrieval.search(query, "hybrid", 2)
    assert result["query"] == query
    assert result["hits"][0]["metadata"]["source_id"] == "mitochondria"
    assert "english" in result["hits"][0]["query_branches"]
    assert len(result["hits"]) <= 2
    retrieval.search(query, "bm25", 2)
    retrieval.search(query, "dense", 2)
    assert calls == [query]
    scoped = retrieval.search(query, "hybrid", 2, document_id=corpus[0]["document"]["id"])
    assert all(h["document_id"] == corpus[0]["document"]["id"] for h in scoped["hits"])


def test_translation_failure_keeps_original_hits_and_warning(services, corpus, monkeypatch):
    retrieval = RetrievalService(
        services.repository, services.retrieval.embedder, settings(services)
    )

    def fail(query):
        raise ProviderError("测试接口不可用")

    monkeypatch.setattr(retrieval.translator, "translate", fail)
    result = retrieval.search("线粒体", "hybrid", 1)
    assert len(result["hits"]) == 1
    assert result["query_translation"]["status"] == "fallback"
    assert "英文检索表达转换失败" in " ".join(result["warnings"])


def test_translation_cache_persists_and_model_change_invalidates(services, monkeypatch):
    calls = []

    def post(url, payload, key, timeout):
        calls.append(payload)
        return {"choices": [{"message": {"content": '{"english_query":"What is ATP?"}'}}]}

    monkeypatch.setattr("biokb.providers.post_json", post)
    first = QueryTranslator(settings(services)).translate("什么是ATP？")
    second = QueryTranslator(settings(services)).translate("什么是ATP？")
    assert not first["cache_hit"] and second["cache_hit"]
    assert len(calls) == 1
    assert calls[0]["response_format"] == {"type": "json_object"}
    QueryTranslator(replace(settings(services), llm_model="different")).translate("什么是ATP？")
    assert len(calls) == 2


def test_ollama_translation_uses_native_json_and_separate_cache(services, monkeypatch):
    calls = []

    def post(url, payload, key, timeout):
        calls.append((url, payload))
        return {"message": {"content": '{"english_query":"What is ATP?"}'}}

    monkeypatch.setattr("biokb.providers.post_json", post)
    cloud = settings(services)
    translator = QueryTranslator(replace(cloud, llm_backend="ollama").validate())
    assert translator.fingerprint != QueryTranslator(cloud).fingerprint
    first = translator.translate("什么是ATP？")
    second = translator.translate("什么是ATP？")
    assert first["english_query"] == "What is ATP?"
    assert second["cache_hit"] and len(calls) == 1
    url, payload = calls[0]
    assert url == cloud.llm_base_url.rstrip("/") + "/api/chat"
    assert payload["format"]["required"] == ["english_query"]
    assert payload["think"] is False


@pytest.mark.parametrize("value", [{}, {"english_query": "答案是中文"}, {"english_query": ""}])
def test_translation_rejects_invalid_output(value):
    with pytest.raises(ProviderError):
        QueryTranslator.validate(value)


def test_empty_library_does_not_request_translation(services, monkeypatch):
    retrieval = RetrievalService(
        services.repository, services.retrieval.embedder, settings(services)
    )

    def unexpected(query):
        pytest.fail("空知识库不应调用模型")

    monkeypatch.setattr(retrieval.translator, "translate", unexpected)
    assert not retrieval.search("待检索问题", "hybrid", 1)["hits"]


def test_failed_translation_is_not_cached(services, monkeypatch):
    replies = iter(["invalid json", '{"english_query":"What is ATP?"}'])

    def post(*args):
        return {"choices": [{"message": {"content": next(replies)}}]}

    monkeypatch.setattr("biokb.providers.post_json", post)
    translator = QueryTranslator(settings(services))
    with pytest.raises(ProviderError):
        translator.translate("什么是ATP？")
    assert not translator.translate("什么是ATP？")["cache_hit"]
