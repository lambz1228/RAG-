"""本地模型来源、查询/文献角色与注意力池化；不下载真实权重。"""

import hashlib
import json
from dataclasses import replace

import pytest

from biokb.config import Settings
from biokb.errors import KnowledgeError, ProviderError
from biokb.local_embedding import LocalE5, masked_mean_pool
from biokb.providers import EmbeddingProvider

REVISION = "a" * 40


@pytest.fixture
def model_directory(tmp_path):
    files = {}
    for name in [
        "model.onnx",
        "tokenizer.json",
        "config.json",
        "tokenizer_config.json",
        "README.md",
    ]:
        raw = b"controlled fixture " + name.encode()
        (tmp_path / name).write_bytes(raw)
        files[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "source_path": name,
        }
    (tmp_path / "MODEL_MANIFEST.json").write_text(
        json.dumps(
            {
                "repository": "intfloat/multilingual-e5-small",
                "revision": REVISION,
                "format": "onnx-qint8-avx512-vnni",
                "files": files,
            }
        )
    )
    return tmp_path


def local_settings(directory):
    return Settings(
        embedding_backend="onnx_e5",
        embedding_model="intfloat/multilingual-e5-small",
        embedding_model_path=str(directory),
        embedding_revision=REVISION,
    )


def test_query_and_document_prefixes_preserve_unicode_and_legacy_behavior(
    model_directory, monkeypatch
):
    recorded = []
    monkeypatch.setattr(
        LocalE5,
        "_infer",
        lambda self, texts: recorded.append(texts) or [[2.0] + [0.0] * 383 for _ in texts],
    )
    provider = EmbeddingProvider(local_settings(model_directory))
    assert provider.embed_batch(["细胞文献"])[0][0] == 1
    assert provider.embed_query("细胞问题")[0] == 1
    assert recorded == [["passage: 细胞文献"], ["query: 细胞问题"]]
    legacy = EmbeddingProvider(Settings())
    assert legacy.embed_query("原有问题") == legacy.embed_batch(["原有问题"])[0]


def test_changed_weights_or_revision_cannot_reuse_model_identity(model_directory):
    original = LocalE5(str(model_directory), REVISION, 2)
    assert original.fingerprint == LocalE5(str(model_directory), REVISION, 1).fingerprint
    with pytest.raises(ProviderError, match="修订"):
        LocalE5(str(model_directory), "b" * 40, 2)
    (model_directory / "model.onnx").write_bytes(b"changed")
    with pytest.raises(ProviderError, match="摘要"):
        LocalE5(str(model_directory), REVISION, 2)


def test_padding_is_excluded_from_mean_pooling():
    np = pytest.importorskip("numpy")
    hidden = np.zeros((1, 3, 384), dtype=np.float32)
    hidden[0, 0, 0], hidden[0, 1, 0], hidden[0, 2, 0] = 2, 4, 999
    assert masked_mean_pool(hidden, [[1, 1, 0]])[0][0] == 3
    with pytest.raises(ProviderError, match="有效词元"):
        masked_mean_pool(hidden, [[0, 0, 0]])
    with pytest.raises(ProviderError, match="形状"):
        masked_mean_pool(hidden, [[1, 0]])


def test_e5_configuration_requires_fixed_source_and_model_path(tmp_path):
    settings = local_settings(tmp_path)
    settings.validate()
    for changed in [
        replace(settings, embedding_revision="main"),
        replace(settings, embedding_model_path=""),
        replace(settings, embedding_model="another-model"),
    ]:
        with pytest.raises(KnowledgeError):
            changed.validate()


def test_retrieval_uses_query_role_and_cache(services, corpus, monkeypatch):
    provider = services.retrieval.embedder
    called = []
    original = provider.embed_query
    monkeypatch.setattr(
        provider, "embed_query", lambda query: called.append(query) or original(query)
    )
    services.retrieval.search("线粒体", "dense", 3)
    services.retrieval.search("线粒体", "hybrid", 3)
    assert called == ["线粒体"]


def test_semantic_term_expansion_preserves_question_and_is_bounded():
    from biokb.text import expand_semantic_query

    query = "真核mRNA的5′帽有哪些作用？"
    expanded, terms = expand_semantic_query(query)
    assert expanded.startswith(query) and "5-prime cap" in terms
    english = "What is the role of the Golgi apparatus?"
    assert expand_semantic_query(english) == (english, [])
    _, many = expand_semantic_query("线粒体内质网细胞核细胞膜高尔基体叶绿体核小体染色质核仁核糖体")
    assert len(many) <= 8


def test_term_expansion_does_not_modify_bm25_or_user_question(services, corpus, monkeypatch):
    from biokb.retrieval import RetrievalService

    settings = replace(services.settings, query_term_expansion=True)
    expanded = RetrievalService(services.repository, services.retrieval.embedder, settings)
    query = "线粒体细胞核"
    before = services.retrieval.search(query, "bm25", 3)
    after = expanded.search(query, "bm25", 3)
    assert before["hits"] == after["hits"] and after["query_expansion"]["terms"] == []
    result = expanded.search(query, "dense", 3)
    assert result["query"] == query and "mitochondria" in result["query_expansion"]["terms"]


def test_exact_question_echo_requires_repair_and_cannot_become_generated():
    from biokb.rag import RAGService

    class Echo:
        def answer(self, query, evidence):
            return {"answer": query + " [1]", "citations": [1], "status": "answered"}

    query = "受控问题是什么？"
    with pytest.raises(ProviderError, match="复述"):
        RAGService.generate_answer(Echo(), query, [{}])

    class Repaired(Echo):
        def repair_answer(self, query, evidence, previous, error):
            assert "复述" in error
            return {"answer": "受控测试答案 [1]", "citations": [1], "status": "answered"}

    trace = {}
    result = RAGService.generate_answer(Repaired(), query, [{}], trace)
    assert result[0] == "受控测试答案 [1]"
    assert trace["attempts"] == 2 and trace["format_repair"]


def test_claims_repair_prompt_keeps_claims_contract(model_directory, monkeypatch):
    from biokb.providers import LanguageModel

    captured = []

    def post(url, payload, key, timeout):
        captured.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "answered",
                                "claims": [{"text": "受控修复回答", "citations": [1]}],
                                "explanation": "",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("biokb.providers.post_json", post)
    settings = replace(
        local_settings(model_directory),
        llm_backend="openai_compatible",
        llm_model="controlled",
        llm_output_mode="claims",
        llm_response_format="json_schema",
    )
    result = LanguageModel(settings).repair_answer(
        "受控问题",
        [{"title": "受控来源", "text": "受控原文"}],
        {"answer": "受控问题 [1]", "citations": [1]},
        "复述问题",
    )
    prompt = captured[0]["messages"][-1]["content"]
    assert "status、claims、explanation" in prompt and "status、answer、citations" not in prompt
    assert result["answer"] == "受控修复回答 [1]"
