"""教材标识、固定来源和正文边界；只使用受控CNXML夹具。"""

import hashlib
import json
from pathlib import Path

import pytest

from biokb.errors import KnowledgeError
from biokb.literature import TOPICS, LiteratureService
from biokb.textbooks import parse_module, source_url
from biokb.web.app import create_app

REVISION = "a" * 40
ITEM = {
    "module_id": "m12345",
    "chapter_number": 4,
    "section_number": 3,
    "chapter_title": "Cells",
    "topic": "cell_basics",
}


def fixture_xml():
    return (
        """<document xmlns="http://cnx.rice.edu/cnxml"><title>Cells</title>
    <metadata xmlns:md="http://cnx.rice.edu/mdml"><md:content-id>m12345</md:content-id></metadata>
    <content><section><title>Structure</title><para>"""
        + "Controlled source body. " * 35
        + """
    <figure><caption>Image is excluded.</caption></figure>Tail is retained.</para>
    <note><para>Sidebar is excluded.</para></note><exercise><solution>Answer key is excluded.</solution></exercise>
    </section></content><glossary><definition><term>Organelle</term><meaning>Defined source term.</meaning></definition></glossary></document>"""
    ).encode()


def test_textbook_body_excludes_exercises_images_and_retains_terms():
    doc = parse_module(fixture_xml(), ITEM, REVISION)
    assert "Tail is retained." in doc["content"]
    assert "Organelle: Defined source term." in doc["content"]
    assert not any(
        x in doc["content"]
        for x in ["Image is excluded", "Sidebar is excluded", "Answer key is excluded"]
    )
    assert doc["license"].endswith("/by-nc-sa/4.0/")
    with pytest.raises(KnowledgeError, match="标识"):
        parse_module(fixture_xml(), {**ITEM, "module_id": "m99999"}, REVISION)


@pytest.mark.parametrize(
    "revision,path",
    [
        ("main", "LICENSE"),
        (REVISION, "../../.env"),
        (REVISION, "https://example.com/file"),
        (REVISION, "modules/m12345/../../LICENSE"),
        (REVISION, "collections/another-book.xml"),
    ],
)
def test_unregistered_textbook_paths_never_become_fetch_urls(revision, path):
    with pytest.raises(KnowledgeError):
        source_url(revision, path)


def test_all_declared_topics_are_in_the_page_and_official_query(services, monkeypatch):
    literature = LiteratureService(services.ingestion, services.settings)
    monkeypatch.setattr(
        literature,
        "_get",
        lambda *args, **kwargs: json.dumps({"hitCount": 0, "resultList": {"result": []}}).encode(),
    )
    page = create_app(services.settings, services).test_client().get("/").get_data(as_text=True)
    assert len(TOPICS) == 18
    for name in TOPICS:
        assert f'value="{name}"' in page
        assert "TITLE_ABS:" in literature.search(topic=name)["query"]


def test_textbook_collection_rejects_changed_cached_license(tmp_path):
    from scripts.collect_foundation_corpus import collect

    registry = (
        Path(__file__).resolve().parents[2]
        / "research/cell-biology-expanded-v2/foundation-sources.json"
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "LICENSE").write_text("changed source license")
    with pytest.raises(KnowledgeError, match="摘要与登记不一致"):
        collect(tmp_path / "result", registry, cache)


def test_layer_merge_preserves_sources_and_rejects_nested_output(tmp_path):
    from scripts.prepare_expanded_corpus import prepare

    source = tmp_path / "source"
    (source / "documents").mkdir(parents=True)
    (source / "raw").mkdir()
    raw = source / "raw/original.xml"
    raw.write_bytes(b"controlled raw source")
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    (source / "documents/sample.json").write_text(
        json.dumps(
            {
                "title": "Controlled document",
                "source_id": "controlled",
                "content": "Controlled corpus source. " * 40,
            }
        )
    )
    with pytest.raises(KnowledgeError, match="来源目录"):
        prepare([source], source / "new")
    output = tmp_path / "combined"
    report = prepare([source], output)
    assert report["ready"] and report["stats"]["documents"] == 1
    assert (output / "database/sources/layer-1/original.xml").read_bytes() == raw.read_bytes()
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before
