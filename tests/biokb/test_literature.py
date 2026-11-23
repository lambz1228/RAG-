"""文献来源、许可、输入边界和从官方记录到实际入库的回归。"""

import json
from pathlib import Path

import pytest

from biokb.errors import KnowledgeError
from biokb.literature import LiteratureService
from biokb.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples/open_demo"


def official_record():
    sample = json.loads((EXAMPLES / "PMC12142011.json").read_text())
    return {
        "id": sample["pmid"],
        "pmid": sample["pmid"],
        "pmcid": sample["pmcid"],
        "doi": sample["doi"],
        "title": sample["title"],
        "pubYear": "2025",
        "abstractText": "Peroxisomal biogenesis factors in breast malignancy. " * 8,
        "isOpenAccess": "Y",
        "authorString": "; ".join(sample["authors"]),
        "authorList": {"author": [{"fullName": author} for author in sample["authors"]]},
        "journalInfo": {"journal": {"title": "Test journal"}},
    }


@pytest.fixture
def literature(services, monkeypatch):
    client = LiteratureService(services.ingestion, services.settings)

    def get(path, params=None, **kwargs):
        if path == "/search":
            return json.dumps(
                {"hitCount": 1, "resultList": {"result": [official_record()]}}
            ).encode()
        return (EXAMPLES / "PMC12142011.xml").read_bytes()

    monkeypatch.setattr(client, "_get", get)
    return client


def test_open_article_identity_license_and_text_are_required():
    xml = (EXAMPLES / "PMC12142011.xml").read_bytes()
    record = official_record()
    text, license_url = LiteratureService.parse_fulltext(xml, record)
    assert len(text) > 1000 and license_url.endswith("/by/4.0/")
    with pytest.raises(KnowledgeError, match="标识"):
        LiteratureService.parse_fulltext(xml, {**record, "pmid": "999"})
    without_license = xml.replace(b"creativecommons.org", b"unconfirmed.example")
    with pytest.raises(KnowledgeError, match="许可"):
        LiteratureService.parse_fulltext(without_license, record)


def test_fulltext_import_retains_provenance_and_deduplicates(literature, services):
    result = literature.import_article("40487257", "peroxisome")
    assert result["imported_type"] == "fulltext"
    assert result["xml_sha256"] and result["source_metadata_sha256"]
    assert len(list((services.settings.data_dir / "sources/europepmc").iterdir())) == 2
    document = services.repository.document(result["document"]["id"])
    assert document["metadata"]["pmid"] == "40487257"
    assert document["metadata"]["topic"] == "peroxisome"
    assert literature.import_article("40487257", "peroxisome")["duplicate"]
    assert services.repository.stats()["documents"] == 1


def test_unconfirmed_license_falls_back_to_explicit_abstract(literature, monkeypatch):
    original = literature._get
    monkeypatch.setattr(
        literature,
        "_get",
        lambda path, params=None: (
            original(path, params).replace(b"creativecommons.org", b"unconfirmed.example")
            if path != "/search"
            else original(path, params)
        ),
    )
    result = literature.import_article("40487257", "peroxisome")
    assert result["imported_type"] == "abstract"
    assert result["warnings"]


@pytest.mark.parametrize(
    "changes",
    [
        {"topic": "invalid"},
        {"year_from": True},
        {"year_to": 1900},
        {"page_size": 100},
        {"open_access": "false"},
        {"keywords": ["test"]},
    ],
)
def test_invalid_search_never_reaches_network(literature, changes):
    with pytest.raises(KnowledgeError):
        literature.search(**changes)


def test_search_keeps_topic_and_access_constraints(literature, monkeypatch):
    captured = []
    original = literature._get

    def get(path, params):
        captured.append(params)
        return original(path, params)

    monkeypatch.setattr(literature, "_get", get)
    result = literature.search(keywords="ATP) OR *")
    assert result["items"][0]["pmid"] == "40487257"
    assert "OPEN_ACCESS:Y" in captured[0]["query"]
    assert '"ATP"' in captured[0]["query"]
    assert "OR *" not in captured[0]["query"]


def test_literature_api_search_and_input_guard(services, monkeypatch):
    app = create_app(services.settings, services)
    client = app.test_client()
    module = app.extensions["literature"]
    monkeypatch.setattr(module, "search", lambda *args: {"items": [], "total": 0})
    assert client.get("/api/literature/topics").json["topics"]["mitochondria"]
    assert client.post("/api/literature/search", json={"topic": "mitochondria"}).json["total"] == 0
    assert (
        client.post("/api/literature/import", json={"pmid": "https://example.com"}).status_code
        == 400
    )


def test_europe_pmc_next_page_uses_returned_cursor(literature, monkeypatch):
    def get(path, params):
        pmid = "123" if params["cursorMark"] == "*" else "456"
        return json.dumps(
            {"hitCount": 2, "nextCursorMark": "YWJjZA==", "resultList": {"result": [{"id": pmid}]}}
        ).encode()

    monkeypatch.setattr(literature, "_get", get)
    first = literature.search(page_size=1)
    second = literature.search(page_size=1, page=2, cursor=first["next_cursor"])
    assert first["items"][0]["pmid"] == "123"
    assert second["items"][0]["pmid"] == "456" and second["next_cursor"] is None
    with pytest.raises(KnowledgeError, match="游标"):
        literature.search(page=2)
