import hashlib
import io
import json
import zipfile

import pytest

from biokb.errors import KnowledgeError, ProviderError
from biokb.text import normalize_text, split_text, tokenize


def test_mixed_chinese_gene_symbols_are_separate_tokens():
    tokens = tokenize("配体EGF结合受体EGFR，随后激活RAS")
    assert {"egf", "egfr", "ras"} <= set(tokens)
    assert "线粒" in tokenize("线粒体")
    assert "mitochondria" in tokenize("线粒体")


def test_chunks_cover_text_and_preserve_offsets():
    text = normalize_text("甲乙丙丁。\r\n" * 200 + "最后一段研究结果。")
    chunks = split_text(text, 200, 40)
    coverage = set()
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
        assert len(chunk.text) <= 200
        coverage.update(range(chunk.start, chunk.end))
    assert all(i in coverage for i, c in enumerate(text) if not c.isspace())
    assert chunks[-1].end == len(text)


def test_ingestion_persists_vectors_and_original_bytes(services):
    content = "EGFR信号通路的研究笔记。配体EGF结合EGFR后触发下游信号。".encode()
    result = services.ingestion.ingest("../../signal.txt", content)
    doc = services.repository.document(result["document"]["id"])
    assert doc["filename"] == "signal.txt"
    assert doc["chunks"]
    path = services.settings.data_dir / "uploads" / (hashlib.sha256(content).hexdigest() + ".txt")
    assert path.read_bytes() == content
    assert services.repository.stats()["vectors"] == len(doc["chunks"])


def test_duplicate_upload_does_not_duplicate_index(services):
    data = b"EGFR activates RAS in this test document."
    first = services.ingestion.ingest("one.txt", data)
    second = services.ingestion.ingest("two.txt", data)
    assert second["duplicate"] is True
    assert first["document"]["id"] == second["document"]["id"]
    assert services.repository.stats()["documents"] == 1


def test_failed_embedding_leaves_no_partial_document(services, monkeypatch):
    def fail(texts):
        raise ProviderError("test failure")

    monkeypatch.setattr(services.ingestion.embedder, "embed_batch", fail)
    with pytest.raises(ProviderError):
        services.ingestion.ingest("bad.txt", b"A complete test document whose embedding fails.")
    assert services.repository.stats()["documents"] == 0
    assert services.repository.stats()["chunks"] == 0


def test_json_metadata_and_directory_pairs_are_preserved(services, tmp_path):
    source = tmp_path / "corpus"
    source.mkdir()
    payload = {
        "title": "Article title",
        "content": "A test document with EGFR signal pathway details.",
        "url": "https://example.org/article/1",
        "topic": "signaling",
    }
    (source / "paper.json").write_text(json.dumps(payload), encoding="utf-8")
    (source / "paper.txt").write_text(payload["content"], encoding="utf-8")
    result = services.ingestion.import_directory(source)
    assert result["imported"] == 1
    doc = services.repository.list_documents()["items"][0]
    assert doc["metadata"]["source_id"] == "paper"
    assert doc["metadata"]["url"] == payload["url"]


@pytest.mark.parametrize(
    "name,content",
    [
        ("x.exe", b"whatever"),
        ("x.txt", b""),
        ("x.json", b'{"value":1}'),
        ("x.txt", b"\x00\x01" * 100),
    ],
)
def test_invalid_files_fail_explicitly(services, name, content):
    with pytest.raises(KnowledgeError):
        services.ingestion.ingest(name, content)


def test_source_url_cannot_be_javascript(services):
    with pytest.raises(KnowledgeError):
        services.ingestion.ingest(
            "paper.txt",
            b"Test article text with sufficient length.",
            {"url": "javascript:alert(1)"},
        )


def test_docx_tables_are_extracted(services):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Research notes for protein signaling.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "EGFR"
    table.cell(0, 1).text = "RAS"
    buffer = io.BytesIO()
    doc.save(buffer)
    result = services.ingestion.ingest("paper.docx", buffer.getvalue())
    text = services.repository.document(result["document"]["id"])["text"]
    assert "EGFR | RAS" in text


def test_archive_decompression_limit(services):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("too-large.xml", b"0" * (101 * 1024 * 1024))
    with pytest.raises(KnowledgeError, match="体积过大"):
        services.ingestion.ingest("large.docx", buffer.getvalue())
