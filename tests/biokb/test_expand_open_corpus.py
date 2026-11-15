"""开放全文采集中的许可、标识和批次完整性校验。"""

import hashlib
import json
from pathlib import Path

import pytest

from biokb.ingestion import extract_document
from scripts.collect_seeded_corpus import validate_seeds
from scripts.discover_gap_candidates import missing_groups
from scripts.expand_open_corpus import (
    accepted_count,
    article_from_fulltext,
    build_topic_query,
    clean_text,
    parse_fulltext,
    topic_relevant,
    verify_batch,
)
from scripts.verify_pubmed_snapshot import parse_records

ARTICLE = {"pmid": "12345678", "pmcid": "PMC12345678", "doi": "10.1000/example"}


def test_decodes_and_removes_title_markup() -> None:
    assert clean_text("&lt;i&gt;Aronia&lt;/i&gt; extract") == "Aronia extract"


def test_topic_screen_uses_title_and_abstract() -> None:
    article = {"title": "ER glycosylation", "abstractText": "GXYLT1 controls protein transport."}
    assert not topic_relevant(article, "golgi")
    article["abstractText"] = "Golgi trafficking depends on GXYLT1."
    assert topic_relevant(article, "golgi")


def test_year_quota_is_independent_for_each_publication_year() -> None:
    items = [
        {"topic": "mitophagy", "origin": "new_paper", "year": "2021"},
        {"topic": "mitophagy", "origin": "new_paper", "year": "2022"},
        {"topic": "mitophagy", "origin": "existing_abstract", "year": "2021"},
    ]
    assert accepted_count(items, "mitophagy", "new_paper", 2021) == 1
    assert accepted_count(items, "mitophagy", "new_paper", 2022) == 1
    assert accepted_count(items, "mitophagy", "new_paper", 2023) == 0
    assert accepted_count(items, "mitophagy", "new_paper", None) == 2
    assert "PUB_YEAR:[2021 TO 2021]" in build_topic_query("mitophagy", 2021)


def test_jats_metadata_uses_electronic_publication_year() -> None:
    raw = (
        '<article article-type="research-article"><front>'
        "<journal-meta><journal-title>Cell Biology</journal-title></journal-meta>"
        '<article-meta><article-id pub-id-type="pmid">12345678</article-id>'
        '<article-id pub-id-type="pmcid">PMC12345678</article-id>'
        '<article-id pub-id-type="doi">10.1000/example</article-id>'
        "<title-group><article-title>Golgi <italic>trafficking</italic> mechanism</article-title></title-group>"
        '<pub-date pub-type="ppub"><year>2023</year></pub-date>'
        '<pub-date pub-type="epub"><year>2022</year></pub-date>'
        "<abstract><p>" + "Golgi transport and glycosylation. " * 30 + "</p></abstract>"
        '<contrib contrib-type="author"><name><given-names>Ada</given-names>'
        "<surname>Chen</surname></name></contrib>"
        '<license href="https://creativecommons.org/licenses/by/4.0/" />'
        "</article-meta></front><body><p>"
        + "Experimental evidence. " * 80
        + "</p></body></article>"
    ).encode()
    article = article_from_fulltext(raw)
    assert article["title"] == "Golgi trafficking mechanism"
    assert article["pubYear"] == "2022"
    assert article["authorList"]["author"] == [{"fullName": "Ada Chen"}]
    assert parse_fulltext(raw, article)[2] == "https://creativecommons.org/licenses/by/4.0/"


def test_seed_manifest_rejects_duplicate_topic_year() -> None:
    group = {"topic": "golgi", "year": 2022, "q": "Golgi 2022", "ids": ["PMC12345678"]}
    with pytest.raises(ValueError, match="重复"):
        validate_seeds({"groups": [group, group]})


def test_article_authors_in_contrib_group() -> None:
    raw = (
        '<article><front><article-meta>'
        '<article-id pub-id-type="pmid">12345678</article-id>'
        '<article-id pub-id-type="pmcid">PMC12345678</article-id>'
        '<title-group><article-title>Peroxisome assay</article-title></title-group>'
        '<pub-date pub-type="epub"><year>2024</year></pub-date>'
        '<contrib-group content-type="author"><contrib><name>'
        '<surname>Neal</surname><given-names>Maxwell</given-names>'
        '</name></contrib></contrib-group>'
        '<contrib-group content-type="editor"><contrib><name>'
        '<surname>Editor</surname><given-names>Example</given-names>'
        '</name></contrib></contrib-group>'
        '</article-meta></front></article>'
    ).encode()
    article = article_from_fulltext(raw)
    assert article["authorList"]["author"] == [{"fullName": "Maxwell Neal"}]


def test_gap_discovery_only_targets_unfilled_topic_years(tmp_path: Path) -> None:
    (tmp_path / "seed_candidates.json").write_text(
        json.dumps(
            {
                "groups": [
                    {"topic": "golgi", "year": 2021},
                    {"topic": "mitosis", "year": 2022},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "acquisition_report.json").write_text(
        json.dumps({"items": [{"topic": "golgi", "year": "2021"}]}), encoding="utf-8"
    )
    assert missing_groups(tmp_path) == [("mitosis", 2022)]


def test_pubmed_snapshot_ignores_reference_article_ids(tmp_path: Path) -> None:
    xml_path = tmp_path / "pubmed.xml"
    xml_path.write_text(
        "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
        "<PMID>12345678</PMID><Article><PublicationTypeList>"
        "<PublicationType>Review</PublicationType></PublicationTypeList>"
        "<Abstract><AbstractText><ArticleId IdType='doi'>10.1000/reference</ArticleId>"
        "</AbstractText></Abstract></Article></MedlineCitation>"
        "<PubmedData><ArticleIdList><ArticleId IdType='pmc'>PMC12345678</ArticleId>"
        "<ArticleId IdType='doi'>10.1000/article</ArticleId></ArticleIdList>"
        "</PubmedData></PubmedArticle></PubmedArticleSet>",
        encoding="utf-8",
    )
    record = parse_records(xml_path)["12345678"]
    assert record["pmcid"] == "PMC12345678"
    assert record["doi"] == "10.1000/article"
    assert record["publication_types"] == ["Review"]


def test_ingestion_keeps_fulltext_provenance() -> None:
    document = {
        "content": "原文证据" * 100,
        "publication_types": ["research-article", "Journal Article"],
        "acquisition": {"xml_sha256": "a" * 64},
    }
    _, metadata = extract_document(json.dumps(document).encode(), ".json")
    assert metadata["publication_types"] == document["publication_types"]
    assert metadata["xml_sha256"] == "a" * 64


def fulltext_xml(license_markup: str, *, pmcid: str = "PMC12345678") -> bytes:
    body = "细胞器相互作用的实验结果与条件记录。" * 100
    return (
        f'<article><front><article-meta><article-id pub-id-type="pmid">12345678</article-id>'
        f'<article-id pub-id-type="pmcid">{pmcid}</article-id>'
        '<article-id pub-id-type="doi">10.1000/example</article-id>'
        f"{license_markup}</article-meta></front><body><sec><p>{body}</p></sec></body></article>"
    ).encode()


def test_accepts_explicit_cc_by_license() -> None:
    raw = fulltext_xml(
        "<license><license-p>依照 "
        '<ext-link href="https://creativecommons.org/licenses/by/4.0/">CC BY</ext-link>'
        " 授权。</license-p></license>"
    )
    content, statement, license_url = parse_fulltext(raw, ARTICLE)
    assert len(content) > 1000
    assert "CC BY" in statement
    assert license_url == "https://creativecommons.org/licenses/by/4.0/"


@pytest.mark.parametrize(
    "license_markup",
    [
        '<license href="https://creativecommons.org/licenses/by-nc/4.0/" />',
        "<license><license-p>CC BY，未给出许可链接</license-p></license>",
        '<license><ext-link href="https://creativecommons.org/licenses/by/4.0/" />'
        '<ext-link href="https://creativecommons.org/licenses/by-nc/4.0/" /></license>',
    ],
)
def test_rejects_unlicensed_or_ambiguous_fulltext(license_markup: str) -> None:
    with pytest.raises(ValueError, match="许可"):
        parse_fulltext(fulltext_xml(license_markup), ARTICLE)


def test_rejects_mismatched_pmcid() -> None:
    with pytest.raises(ValueError, match="pmcid"):
        parse_fulltext(
            fulltext_xml(
                '<license href="https://creativecommons.org/licenses/by/4.0/" />',
                pmcid="PMC99999999",
            ),
            ARTICLE,
        )


def test_resume_requires_unchanged_source_and_files(tmp_path) -> None:
    database = tmp_path / "source.sqlite3"
    database.write_bytes(b"baseline")
    raw_dir = tmp_path / "raw_xml"
    document_dir = tmp_path / "documents"
    raw_dir.mkdir()
    document_dir.mkdir()
    xml = raw_dir / "PMC12345678.xml"
    document = document_dir / "PMC12345678.json"
    xml.write_bytes(b"xml")
    document.write_bytes(b"json")

    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    report = {
        "database": str(database),
        "database_sha256_before": digest(b"baseline"),
        "items": [
            {
                "pmid": "12345678",
                "pmcid": "PMC12345678",
                "xml_sha256": digest(b"xml"),
                "json_sha256": digest(b"json"),
            }
        ],
    }
    verify_batch(report, database, raw_dir, document_dir)
    document.write_bytes(b"changed")
    with pytest.raises(ValueError, match="摘要不一致"):
        verify_batch(report, database, raw_dir, document_dir)
