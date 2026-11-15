"""v26 审计的标识、版本关系、字符区间与摘要核验测试。"""

import json

from scripts.audit_v26_research import normalize_title, parse_pubmed
from scripts.run_v26_machine_experiment import interval_coverage
from scripts.stage_v26_abstract_fulltexts import inspect_xml
from scripts.verify_v26_quality_artifacts import (
    sha256_file,
    verify_manifest,
    verify_sources,
)


def test_pubmed_relation_target_and_publication_years() -> None:
    xml = b"""<PubmedArticleSet><PubmedArticle>
      <MedlineCitation><PMID>123</PMID><Article>
        <ArticleTitle>A <i>gene</i> study.</ArticleTitle>
        <Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
        <ArticleDate><Year>2024</Year></ArticleDate>
        <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
      </Article><CommentsCorrectionsList><CommentsCorrections RefType="ErratumIn">
        <RefSource>Correction note</RefSource><PMID>456</PMID>
      </CommentsCorrections></CommentsCorrectionsList></MedlineCitation>
      <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/example</ArticleId>
        <ArticleId IdType="pmc">PMC100</ArticleId></ArticleIdList></PubmedData>
    </PubmedArticle></PubmedArticleSet>"""
    row = parse_pubmed(xml)["123"]
    assert row["title"] == "A gene study."
    assert row["journal_year"] == "2025"
    assert row["online_year"] == "2024"
    assert row["relations"] == [{"type": "ErratumIn", "target_pmid": "456",
                                "citation": "Correction note"}]
    assert normalize_title("A <i>gene</i> study.") == normalize_title(row["title"])


def test_xml_license_and_identity_are_both_required() -> None:
    xml = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
      <article-id pub-id-type="pmcid">PMC100</article-id>
      <article-id pub-id-type="pmid">123</article-id>
      <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/">
        <license-p>CC BY 4.0</license-p></license></permissions>
      </article-meta></front><body><p>Evidence.</p></body></article>"""
    assert inspect_xml(xml, "PMC100", "123")["candidate_for_next_batch"]
    assert not inspect_xml(xml, "PMC100", "999")["candidate_for_next_batch"]
    assert not inspect_xml(xml.replace(b"<body><p>Evidence.</p></body>", b""),
                           "PMC100", "123")["candidate_for_next_batch"]


def test_interval_coverage_merges_overlapping_chunks() -> None:
    hits = [
        {"document_id": "a", "start_offset": 10, "end_offset": 35},
        {"document_id": "a", "start_offset": 25, "end_offset": 50},
        {"document_id": "b", "start_offset": 30, "end_offset": 70},
    ]
    assert interval_coverage(20, 60, hits, "a") == 0.75


def test_artifact_verifier_detects_tampering(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    artifact = output / "result.json"
    artifact.write_text('{"status":"frozen"}', encoding="utf-8")
    manifest = {"files": {
        artifact.name: {"bytes": artifact.stat().st_size, "sha256": sha256_file(artifact)}
    }}
    (output / "OUTPUT_MANIFEST.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    source = tmp_path / "data" / "source.xml"
    source.parent.mkdir()
    source.write_text("<article />", encoding="utf-8")
    audit = {"source_files_sha256": {"data\\source.xml": sha256_file(source)}}

    assert verify_manifest(output) == []
    assert verify_sources(audit, tmp_path) == []
    artifact.write_text('{"status":"changed"}', encoding="utf-8")
    source.write_text("<changed />", encoding="utf-8")
    assert "摘要或大小不一致：result.json" in verify_manifest(output)
    assert "来源摘要不一致：data\\source.xml" in verify_sources(audit, tmp_path)
