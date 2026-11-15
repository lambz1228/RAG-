"""论文身份、范围隔离、上下文追溯及持久任务的行为验证。"""

import json
import time

import pytest

from biokb.errors import KnowledgeError, NotFoundError
from biokb.papers import build_catalog, identifiers, list_papers, resolve_scope
from biokb.web.app import create_app


def record(key, **metadata):
    return {
        "id": key,
        "title": "同名论文",
        "filename": key + ".json",
        "metadata": {"content_type": "abstract", **metadata},
    }


def test_identifiers_and_paper_versions_are_linked():
    assert identifiers({"doi": "https://doi.org/10.1234/ABC", "pmcid": "pmc123"}) == {
        "doi": "10.1234/abc",
        "pmcid": "PMC123",
    }
    catalog = build_catalog(
        [
            record("abstract", source="europe_pmc", id="123"),
            record("fulltext", pmid="123", doi="10.1234/test", content_type="fulltext"),
            record("other", pmid="456"),
            record("stub", content_type="reference_stub"),
        ]
    )
    assert catalog["summary"]["papers"] == 2
    paper = next(p for p in catalog["items"] if p["has_fulltext"])
    assert paper["document_ids"] == ["fulltext", "abstract"]
    assert paper["preferred_document_id"] == "fulltext"


def test_same_title_and_conflicting_identifiers_do_not_merge():
    catalog = build_catalog(
        [
            record("one", pmid="123", doi="10.1234/same"),
            record("two", pmid="456", doi="10.1234/same"),
            record("three"),
            record("four"),
        ]
    )
    assert len(catalog["items"]) == 4
    assert len({p["id"] for p in catalog["items"]}) == 4
    assert sum(p["identity_conflict"] for p in catalog["items"]) == 2


def test_transitive_identity_links_merge_all_versions():
    catalog = build_catalog(
        [
            record("a", pmid="123"),
            record("b", pmcid="PMC456"),
            record("c", doi="10.1234/link", pmcid="PMC456"),
            record("d", doi="10.1234/link", pmid="123"),
        ]
    )
    assert len(catalog["items"]) == 1
    assert len(catalog["items"][0]["records"]) == 4


@pytest.fixture
def papers(services):
    inputs = [
        (
            "abstract",
            "研究 A 摘要 EGFR ATP. ",
            {"source": "europe_pmc", "id": "123", "content_type": "abstract"},
        ),
        (
            "fulltext",
            "研究 A 全文 EGFR ATP. ",
            {
                "pmid": "123",
                "content_type": "fulltext",
                "authors": ["Researcher A"],
                "doi": "10.1234/a",
            },
        ),
        ("other", "研究 B EGFR 不同结果 ATP. ", {"pmid": "456", "content_type": "abstract"}),
    ]
    return [
        services.ingestion.ingest(
            name + ".json",
            json.dumps(
                {"content": text * 40, "title": name, "year": "2025", "topic": "signaling", **meta}
            ).encode(),
        )["document"]
        for name, text, meta in inputs
    ]


def test_catalog_query_and_scope_are_paper_based(services, papers):
    result = list_papers(services.repository, query="researcher a", fulltext=True)
    assert result["total"] == 1
    assert result["summary"]["papers"] == 2
    assert set(result["items"][0]["document_ids"]) == {papers[0]["id"], papers[1]["id"]}
    scope = resolve_scope(services.repository, [result["items"][0]["id"]])
    assert set(scope["document_ids"]) == {papers[0]["id"], papers[1]["id"]}


@pytest.mark.parametrize("bad", ["all", {}, [True], ["paper_bad"], ["paper_" + "a" * 24] * 21])
def test_invalid_scope_is_not_silently_unrestricted(services, bad):
    with pytest.raises(KnowledgeError):
        resolve_scope(services.repository, bad)


def test_missing_scope_fails_even_when_library_has_hits(services, papers):
    with pytest.raises(NotFoundError):
        services.retrieval.search("EGFR", "bm25", paper_ids=["paper_" + "f" * 24])


def test_scope_limits_search_answer_and_retry_payload(services, papers):
    paper = list_papers(services.repository, query="fulltext")["items"][0]
    expected = set(paper["document_ids"])
    hits = services.retrieval.search("EGFR ATP", "bm25", paper_ids=[paper["id"]])["hits"]
    assert hits and all(hit["document_id"] in expected for hit in hits)
    answer = services.rag.ask("EGFR ATP", "bm25", paper_ids=[paper["id"]], context_neighbors=1)
    assert answer["citations"] and all(c["document_id"] in expected for c in answer["citations"])
    assert answer["context"]["source_count"] == 1
    assert answer["retrieval"]["scope"]["papers"][0]["id"] == paper["id"]
    saved = services.repository.run(answer["id"])
    assert saved["retrieval"]["scope"] == answer["retrieval"]["scope"]
    client = create_app(services.settings, services).test_client()
    job = client.post(
        "/api/ask",
        json={
            "query": "EGFR ATP",
            "strategy": "bm25",
            "paper_ids": [paper["id"]],
            "context_neighbors": 1,
        },
    ).get_json()["job"]
    assert job["retry"]["data"]["paper_ids"] == [paper["id"]]
    assert job["retry"]["data"]["context_neighbors"] == 1
    deadline = time.monotonic() + 5
    while (
        services.jobs.get(job["id"])["status"] in {"queued", "running"}
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert services.jobs.get(job["id"])["status"] == "completed"


def test_context_windows_match_exact_source_and_budget(services, papers):
    doc = services.repository.document(papers[1]["id"])
    hit = next(
        h
        for h in services.retrieval.search("EGFR ATP", "bm25", 50)["hits"]
        if h["document_id"] == doc["id"]
    )
    expanded = services.repository.evidence_window(hit, 1)
    assert len(expanded["text"]) > len(hit["text"])
    assert expanded["text"] == doc["text"][expanded["start_offset"] : expanded["end_offset"]]
    assert hit["chunk_id"] in expanded["context_chunk_ids"]
    answer = services.rag.ask("EGFR ATP", "bm25", context_neighbors=2)
    assert answer["context"]["used_chars"] <= services.settings.max_context_chars
    for citation in answer["citations"]:
        source = services.repository.document(citation["document_id"])
        assert citation["text"] == source["text"][citation["start_offset"] : citation["end_offset"]]


def test_single_paper_keeps_sixth_method_chunk_in_evidence(services, monkeypatch):
    """固定候选排名验证证据选取；不把替身回答当成学术正确性评分。"""
    paper_id = "paper_" + "a" * 24
    texts = [f"其他试验步骤 {index}：处理 48 小时。" for index in range(1, 6)]
    texts.append("脂肪变性诱导：油酸与棕榈酸 2:1，处理 24 小时，工作浓度 1 mM。")
    hits = [
        {
            "chunk_id": f"method_{index}",
            "document_id": "method_fulltext",
            "paper_id": paper_id,
            "title": "测试论文",
            "text": value,
            "start_offset": index * 200,
            "end_offset": index * 200 + len(value),
        }
        for index, value in enumerate(texts, 1)
    ]

    def ranked_hits(query, strategy, top_k, **kwargs):
        assert kwargs["paper_ids"] == [paper_id]
        return {
            "query": query,
            "strategy": strategy,
            "hits": hits[:top_k],
            "warnings": [],
            "index_revision": services.repository.revision,
            "scope": {
                "kind": "papers",
                "papers": [{"id": paper_id, "title": "测试论文", "identifiers": {}}],
                "document_ids": ["method_fulltext"],
            },
        }

    monkeypatch.setattr(services.retrieval, "search", ranked_hits)
    monkeypatch.setattr(services.repository, "evidence_window", lambda hit, neighbors: hit)
    answer = services.rag.ask("脂肪变性诱导条件", "bm25", 10, paper_ids=[paper_id])
    assert answer["mode"] == "extractive"
    assert len(answer["citations"]) == 6
    assert answer["citations"][5]["text"] == texts[5]
    assert "24 小时" in answer["answer"] and "1 mM" in answer["answer"]
    assert answer["context"]["source_count"] == 1


def test_paper_api_validates_scope_and_enforces_auth(services, papers):
    from dataclasses import replace

    client = create_app(replace(services.settings, api_token="test-token"), services).test_client()
    assert client.get("/api/papers").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/api/papers?fulltext=true", headers=headers).get_json()["total"] == 1
    assert client.get("/api/papers?fulltext=invalid", headers=headers).status_code == 400
    assert (
        client.post(
            "/api/ask", headers=headers, json={"query": "EGFR", "context_neighbors": True}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/ask", headers=headers, json={"query": "EGFR", "paper_ids": ["paper_" + "f" * 24]}
        ).status_code
        == 404
    )
