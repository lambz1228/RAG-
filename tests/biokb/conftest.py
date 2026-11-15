import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from biokb.config import Settings
from biokb.container import build_services


@pytest.fixture
def services(tmp_path):
    value = build_services(Settings(data_dir=tmp_path, chunk_size=200, chunk_overlap=30))
    yield value
    value.jobs.close()


@pytest.fixture
def corpus(services):
    documents = [
        (
            "egfr.txt",
            "EGF binds EGFR. EGFR activates RAS and RAF in a cell signaling pathway. MEK activates ERK.",
            "egfr",
        ),
        (
            "mitochondria.txt",
            "Mitochondria generate ATP by oxidative phosphorylation. Mitochondrial membranes support energy metabolism.",
            "mitochondria",
        ),
        (
            "autophagy.txt",
            "Autophagy delivers cellular cargo to lysosomes. Lysosomal degradation supports recycling of cellular material.",
            "autophagy",
        ),
    ]
    return [
        services.ingestion.ingest(name, text.encode(), {"source_id": source, "title": name})
        for name, text, source in documents
    ]
