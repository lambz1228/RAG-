"""资料质量、可靠拒答、顺序解析、筛选和任务恢复的行为回归。"""

import hashlib
import io
import json
import sqlite3
import time
from dataclasses import replace

import pytest
from docx import Document

from biokb.config import secret
from biokb.errors import KnowledgeError, ProviderError
from biokb.ingestion import extract_document
from biokb.jobs import JobManager
from biokb.providers import EmbeddingProvider, LanguageModel
from biokb.rag import RAGService
from biokb.reranking import Reranker
from biokb.retrieval import RetrievalService
from biokb.revisions import create_revision
from biokb.storage import Repository
from biokb.web.app import create_app


def test_docx_preserves_interleaved_table_order():
    document = Document()
    document.add_paragraph("背景段落")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "中间数据表"
    document.add_paragraph("结论段落")
    raw = io.BytesIO()
    document.save(raw)
    text, _ = extract_document(raw.getvalue(), ".docx")
    assert text.index("背景") < text.index("数据表") < text.index("结论")


@pytest.mark.parametrize("status", ["answered", "partial", "insufficient"])
def test_model_answer_status_is_preserved(services, corpus, status):
    class Model:
        def answer(self, query, evidence):
            return {
                "status": status,
                "answer": "证据不足，缺少实验条件"
                if status == "insufficient"
                else "EGFR 参与信号转导 [1]",
                "citations": [] if status == "insufficient" else [1],
            }

    settings = replace(services.settings, llm_backend="ollama", llm_model="fixture")
    answer = RAGService(services.repository, services.retrieval, Model(), settings).ask(
        "EGFR", "bm25"
    )
    assert (
        answer["mode"]
        == {"answered": "generated", "partial": "partial", "insufficient": "insufficient_evidence"}[
            status
        ]
    )
    if status == "insufficient":
        assert not any(c["used"] for c in answer["citations"])
        assert "不存在" not in str(answer["warnings"])


def test_invalid_abstention_cannot_smuggle_citations():
    with pytest.raises(ProviderError):
        RAGService._validate_answer(
            {"status": "insufficient", "answer": "事实 [1]", "citations": [1]}, 1
        )


def test_placeholder_is_preserved_but_excluded(services):
    item = services.ingestion.ingest(
        "stub.txt",
        "细胞生物学第4章《内膜系统》的细分内容。".encode(),
        {"source": "baidu_baike_online", "title": "内质网"},
    )
    assert services.repository.stats()["documents"] == 1
    assert item["document"]["metadata"]["quality"]["eligible"] is False
    assert not services.retrieval.search("内质网", "bm25")["hits"]
    assert services.repository.document(item["document"]["id"])["text"]


def test_filters_apply_to_search_answer_and_listing(services):
    for year in ["2024", "2025"]:
        services.ingestion.ingest(
            year + ".txt",
            (year + " EGFR activates RAS.").encode(),
            {"year": year, "topic": "signaling", "content_type": "abstract"},
        )
    client = create_app(services.settings, services).test_client()
    assert client.get("/api/documents?year=2024").get_json()["total"] == 1
    hits = client.post(
        "/api/search", json={"query": "EGFR", "strategy": "bm25", "filters": {"year": "2025"}}
    ).get_json()["hits"]
    assert len(hits) == 1 and hits[0]["metadata"]["year"] == "2025"
    assert (
        client.post("/api/search", json={"query": "EGFR", "filters": {"year": []}}).status_code
        == 400
    )
    answer = services.rag.ask("EGFR", "bm25", filters={"year": "2024"})
    assert all(hit["metadata"]["year"] == "2024" for hit in answer["citations"])


def test_reranker_reorders_candidates_and_records_fingerprint(services, corpus, monkeypatch):
    settings = replace(services.settings, reranker_backend="compatible", reranker_model="fixture")

    def response(url, payload, key, timeout):
        return {
            "results": [
                {"index": i, "relevance_score": float(i)} for i in range(len(payload["documents"]))
            ]
        }

    monkeypatch.setattr("biokb.reranking.post_json", response)
    retrieval = RetrievalService(services.repository, services.retrieval.embedder, settings)
    before = retrieval.search("EGFR mitochondrial autophagy ATP", "hybrid", 3)["hits"]
    after = retrieval.search("EGFR mitochondrial autophagy ATP", "hybrid_rerank", 3)
    assert after["hits"][0]["chunk_id"] == before[-1]["chunk_id"]
    assert after["reranker_fingerprint"]


@pytest.mark.parametrize(
    "rows",
    [[{"index": 0, "relevance_score": float("nan")}], [{"index": True, "relevance_score": 1}], []],
)
def test_reranker_rejects_invalid_results(services, monkeypatch, rows):
    settings = replace(services.settings, reranker_backend="compatible", reranker_model="fixture")
    monkeypatch.setattr("biokb.reranking.post_json", lambda *args: {"results": rows})
    with pytest.raises(ProviderError):
        Reranker(settings).rank("EGFR", [{"title": "title", "text": "evidence", "chunk_id": "one"}])


def test_ollama_embedding_disables_silent_truncation(services, monkeypatch):
    payloads = []

    def response(url, payload, key, timeout):
        payloads.append(payload)
        return {"embeddings": [[1, 0]]}

    monkeypatch.setattr("biokb.providers.post_json", response)
    EmbeddingProvider(
        replace(services.settings, embedding_backend="ollama", embedding_model="test")
    ).embed_batch(["document"])
    assert payloads[0]["truncate"] is False


def test_jobs_recover_interrupted_and_completed_records(services):
    services.repository.save_job(
        {
            "id": "before_restart",
            "kind": "answer",
            "status": "running",
            "stage": "生成",
            "progress": 50,
            "created_at": 1,
        }
    )
    services.repository.save_job(
        {
            "id": "done",
            "kind": "answer",
            "status": "completed",
            "stage": "完成",
            "progress": 100,
            "created_at": 0,
            "result": {"answer": "kept"},
        }
    )
    jobs = JobManager(repository=services.repository)
    try:
        assert jobs.get("before_restart")["status"] == "interrupted"
        assert jobs.get("done")["result"]["answer"] == "kept"
        assert "result" not in next(row for row in jobs.recent() if row["id"] == "done")
    finally:
        jobs.close()


def test_revision_keeps_original_database_and_raw_files(services, corpus, tmp_path):
    source = services.settings.data_dir
    before = hashlib.sha256(services.settings.database_path.read_bytes()).hexdigest()
    target = tmp_path.parent / (tmp_path.name + "-revision")
    report = create_revision(source, target)
    assert hashlib.sha256(services.settings.database_path.read_bytes()).hexdigest() == before
    assert report["documents"] == 3 and report["eligible"] == 3
    assert Repository(target / "knowledge.sqlite3").stats()["documents"] == 3
    for path in (source / "uploads").iterdir():
        assert path.read_bytes() == (target / "uploads" / path.name).read_bytes()
    with pytest.raises(KnowledgeError):
        create_revision(source, target)


@pytest.mark.parametrize("backup_fails", [False, True])
def test_revision_releases_connections_even_when_backup_fails(
    services, tmp_path, monkeypatch, backup_fails
):
    source = services.settings.data_dir
    before = hashlib.sha256(services.settings.database_path.read_bytes()).hexdigest()
    target = tmp_path.parent / (tmp_path.name + "-revision")
    connections = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def backup(self, destination, **kwargs):
            if backup_fails:
                raise sqlite3.OperationalError("测试备份中断")
            return super().backup(destination, **kwargs)

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    if backup_fails:
        with pytest.raises(sqlite3.OperationalError, match="测试备份中断"):
            create_revision(source, target)
    else:
        create_revision(source, target)
    assert hashlib.sha256(services.settings.database_path.read_bytes()).hexdigest() == before
    assert len(connections) >= 2
    # 保留连接引用，保证文件锁释放不依赖垃圾回收或解释器退出。
    try:
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize("value", [[], {}, True, 5])
def test_malformed_quality_type_is_business_error(services, value):
    with pytest.raises(KnowledgeError):
        services.ingestion.ingest(
            "bad.json",
            json.dumps(
                {"content": "EGFR is a test document with enough text.", "content_type": value}
            ).encode(),
        )


@pytest.mark.parametrize("value", [[], {"status": []}, {"status": {}}])
def test_malformed_model_state_is_provider_error(value):
    with pytest.raises(ProviderError):
        RAGService._validate_answer(value, 1)


def test_compatible_json_mode_is_explicit_and_keeps_validation(services, monkeypatch):
    payloads = []

    def respond(url, payload, key, timeout):
        payloads.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"status": "answered", "answer": "正文漏标引用", "citations": [1]}
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr("biokb.providers.post_json", respond)
    settings = replace(
        services.settings,
        llm_backend="openai_compatible",
        llm_model="test",
        llm_response_format="json_object",
    )
    model = LanguageModel(settings)
    result = model.answer("问题", [{"title": "资料", "text": "测试内容"}])
    assert payloads[0]["response_format"] == {"type": "json_object"}
    with pytest.raises(ProviderError):
        RAGService._validate_answer(result, 1)
    assert (
        model.fingerprint
        != LanguageModel(replace(settings, llm_response_format="text")).fingerprint
    )


def test_secret_reference_and_explicit_value_precedence(monkeypatch):
    monkeypatch.setenv("BIOKB_LLM_API_KEY", "")
    monkeypatch.setenv("BIOKB_LLM_API_KEY_ENV", "BIOKB_TEST_SECRET")
    monkeypatch.setenv("BIOKB_TEST_SECRET", "test-only-value")
    assert secret("BIOKB_LLM_API_KEY") == "test-only-value"
    monkeypatch.setenv("BIOKB_LLM_API_KEY", "explicit-only-value")
    assert secret("BIOKB_LLM_API_KEY") == "explicit-only-value"


def test_query_vector_cache_is_bounded_per_provider(services, corpus):
    class Embedder:
        fingerprint = services.retrieval.embedder.fingerprint
        calls = 0

        def embed_batch(self, texts):
            self.calls += 1
            return services.retrieval.embedder.embed_batch(texts)

    provider = Embedder()
    retrieval = RetrievalService(services.repository, provider, services.settings)
    retrieval.search("EGFR", "dense")
    retrieval.search("EGFR", "hybrid")
    assert provider.calls == 1
    for index in range(128):
        retrieval.search(f"EGFR {index}", "dense")
    retrieval.search("EGFR", "dense")
    assert provider.calls == 130


def test_restart_retry_keeps_original_and_creates_new_result(services, corpus):
    services.repository.save_job(
        {
            "id": "restart",
            "kind": "answer",
            "status": "running",
            "stage": "生成",
            "progress": 30,
            "created_at": 1,
            "retry": {"kind": "answer", "data": {"query": "EGFR", "strategy": "bm25"}},
        }
    )
    services.jobs.close()
    services.jobs = JobManager(repository=services.repository)
    client = create_app(services.settings, services).test_client()
    response = client.post("/api/jobs/restart/retry", json={})
    assert response.status_code == 202
    job_id = response.get_json()["job"]["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = services.jobs.get(job_id)
        if job["status"] == "completed":
            break
        time.sleep(0.01)
    assert job["result"]["mode"] == "extractive"
    assert services.jobs.get("restart")["status"] == "interrupted"
    assert client.post(f"/api/jobs/{job_id}/retry", json={}).status_code == 400


def test_job_retention_does_not_exceed_100(services):
    for index in range(100):
        services.repository.save_job({"id": str(index), "status": "completed", "created_at": index})
    manager = JobManager(repository=services.repository)
    try:
        job = manager.submit("test", lambda progress: "done")
        assert len(manager._jobs) == 100
        assert len(services.repository.load_jobs()) == 100
        assert job["id"] in manager._jobs
    finally:
        manager.close()


@pytest.mark.parametrize("valid", [True, False])
def test_format_repair_is_bounded_and_never_bypasses_validation(services, corpus, valid):
    class Model:
        calls = 0

        def answer(self, query, evidence):
            self.calls += 1
            return {"answer": "缺少正文引用", "citations": [1]}

        def repair_answer(self, query, evidence, previous, error):
            self.calls += 1
            assert previous["answer"] == "缺少正文引用"
            return {"answer": "受证据支持的内容 [1]", "citations": [1] if valid else [99]}

    model = Model()
    settings = replace(services.settings, llm_backend="openai_compatible", llm_model="test")
    result = RAGService(services.repository, services.retrieval, model, settings).ask(
        "EGFR", "bm25"
    )
    assert model.calls == 2
    assert result["mode"] == ("generated" if valid else "provider_fallback")
    assert result["generation_validation"]["attempts"] == 2
    assert len(result["generation_validation"]["validation_errors"]) == (1 if valid else 2)
