"""验证 PubMed 直连和中文题录导入的来源与证据边界。"""

import hashlib
import io
import time

import pytest

from biokb.bibliography import import_export
from biokb.errors import KnowledgeError
from biokb.pubmed import PubMedService
from biokb.web.app import create_app

PUBMED_XML = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
<ArticleTitle>Mitochondrial <i>ATP</i> synthesis</ArticleTitle>
<Journal><Title>Test journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
<AuthorList><Author><ForeName>Jane</ForeName><LastName>Doe</LastName></Author></AuthorList>
<Abstract><AbstractText Label="RESULTS">Mitochondria produce ATP through oxidative phosphorylation.</AbstractText></Abstract>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1234/test</ArticleId>
<ArticleId IdType="pmc">PMC12345</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""


def wait_job(client, response):
    assert response.status_code == 202
    job_id = response.get_json()["job"]["id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
        if job["status"] in {"completed", "failed"}:
            assert job["status"] == "completed", job
            return job["result"]
        time.sleep(0.02)
    pytest.fail("入库任务超时")


def test_pubmed_search_and_import_use_ncbi_fields_and_verify_pmid(services, monkeypatch):
    calls = []

    def request(self, operation, params):
        calls.append((operation, params))
        if operation == "esearch.fcgi":
            return b'{"esearchresult":{"count":"30","idlist":["12345"]}}'
        return PUBMED_XML

    monkeypatch.setattr(PubMedService, "_get", request)
    client = create_app(services.settings, services).test_client()
    response = client.post("/api/literature/search", json={"source": "pubmed", "page": 2})
    assert response.status_code == 200
    assert response.json["items"][0]["title"] == "Mitochondrial ATP synthesis"
    assert calls[0][0] == "esearch.fcgi" and calls[0][1]["retstart"] == 10
    assert "mitochondria[Title/Abstract]" in calls[0][1]["term"]
    assert calls[1][0] == "efetch.fcgi" and calls[1][1]["id"] == "12345"
    result = wait_job(
        client, client.post("/api/literature/import", json={"source": "pubmed", "pmid": "12345"})
    )
    meta = result["document"]["metadata"]
    digest = hashlib.sha256(PUBMED_XML).hexdigest()
    assert (meta["source"], meta["doi"], meta["content_type"]) == (
        "pubmed",
        "10.1234/test",
        "abstract",
    )
    assert meta["source_metadata_sha256"] == digest
    assert (
        services.settings.data_dir / "sources" / "pubmed" / f"PMID12345.xml-{digest}"
    ).read_bytes() == PUBMED_XML
    assert (
        client.post(
            "/api/literature/search", json={"source": "pubmed", "open_access": True}
        ).status_code
        == 400
    )
    with pytest.raises(KnowledgeError, match="未找到所选 PMID"):
        PubMedService(services.ingestion, services.settings).import_article("99999", "mitochondria")


def test_cnki_tagged_import_archives_file_and_excludes_stubs_from_evidence(services):
    raw = (
        "%0 Journal Article\n%T 线粒体能量代谢\n%A 张三\n%D 2023\n"
        "%X 线粒体在氧化磷酸化过程中合成ATP。\n"
        "%0 Journal Article\n%T 叶绿体的光合作用\n%J 示例期刊\n"
    ).encode("utf-16")
    client = create_app(services.settings, services).test_client()
    for expected in (0, 2):
        response = client.post(
            "/api/literature/import-export",
            data={"source": "cnki", "topic": "mitochondria", "file": (io.BytesIO(raw), "cnki.txt")},
        )
        result = wait_job(client, response)
        assert result["duplicates"] == expected
        assert result["reference_stubs"] == result["abstracts"] == (0 if expected else 1)
    docs = services.repository.document_catalog()
    assert {doc["metadata"]["content_type"] for doc in docs} == {"reference_stub", "abstract"}
    assert not services.retrieval.search("叶绿体的光合作用", "bm25")["hits"]
    digest = hashlib.sha256(raw).hexdigest()
    assert (
        services.settings.data_dir / "sources" / "cnki" / f"export-{digest}"
    ).read_bytes() == raw


@pytest.mark.parametrize("source", ["wanfang", "vip", "sinomed"])
def test_ris_import_reports_missing_title_without_dropping_valid_records(services, source):
    raw = (
        "TY  - JOUR\nTI  - 细胞核内的蛋白质\nAU  - 李四\nAB  - 细胞核中包含多种功能不同的蛋白质。\n"
        "ER  -\nTY  - JOUR\nAU  - 无题名\nER  -\n"
    ).encode("gb18030")
    result = import_export(services.ingestion, services.settings, raw, source, "cell_basics")
    assert result["abstracts"] == 1
    assert result["failed"] == [{"record": 2, "error": "缺少题名"}]
    assert services.repository.document_catalog()[0]["metadata"]["source"] == source
