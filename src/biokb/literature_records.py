"""文献原始记录归档与题录入库。"""

import hashlib
import json
import os
import tempfile


def archive_record(data_dir, source: str, name: str, raw: bytes) -> str:
    folder = data_dir / "sources" / source
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    target = folder / f"{name}-{digest}"
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            handle.write(raw)
        try:
            os.replace(handle.name, target)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
    return digest


def ingest_record(ingestion, record: dict, provenance: dict, progress=None) -> dict:
    payload = dict(record)
    abstract = payload.pop("abstract", "").strip()
    kind = "abstract" if len(abstract) >= 10 else "reference_stub"
    payload.update(
        content=abstract
        if kind == "abstract"
        else "仅有文献题录，摘要待补充。\n" + payload["title"],
        content_type=kind,
    )
    result = ingestion.ingest(
        payload["source_id"] + ".json",
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        metadata=provenance,
        progress=progress,
    )
    return {**result, "imported_type": kind}
