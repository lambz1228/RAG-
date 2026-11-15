"""按固定修订和逐文件摘要取得开放教材基础语料，保留独立许可与原始CNXML。"""

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.errors import KnowledgeError  # noqa: E402
from biokb.textbooks import LICENSE_URL, REPOSITORY, fetch_file, parse_module, source_url  # noqa: E402


def collect(output, registry, source_cache=None):
    plan = json.loads(registry.read_text(encoding="utf-8"))
    required = {"LICENSE", "collections/biology-2e.collection.xml", "modules/m66425/index.cnxml"}
    if (
        plan.get("repository") != REPOSITORY
        or plan.get("license") != LICENSE_URL
        or {x["path"] for x in plan["supporting_files"]} != required
        or len({x["module_id"] for x in plan["sources"]}) != len(plan["sources"])
    ):
        raise KnowledgeError("教材登记缺少固定来源、许可依据或唯一模块标识")
    output.mkdir(parents=True, exist_ok=False)
    raw_root, documents = output / "raw", output / "documents"
    raw_root.mkdir()
    documents.mkdir()
    revision = plan["revision"]
    report = {"scope": "开放教材来源及文本技术核验，未作人工学术复核", "sources": [], "errors": []}

    def acquire(path, expected):
        source_url(revision, path)
        raw = (source_cache / path).read_bytes() if source_cache else fetch_file(revision, path)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise KnowledgeError("教材文件摘要与登记不一致：" + path)
        target = raw_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return raw

    for item in plan["supporting_files"]:
        acquire(item["path"], item["sha256"])
    license_text = (raw_root / "LICENSE").read_text(encoding="utf-8")
    collection = ET.fromstring((raw_root / "collections/biology-2e.collection.xml").read_bytes())
    licenses = collection.findall(".//{http://cnx.rice.edu/mdml}license")
    if "Attribution-NonCommercial-ShareAlike 4.0 International" not in license_text or not any(
        node.get("url", "").replace("http://", "https://").rstrip("/") == LICENSE_URL.rstrip("/")
        for node in licenses
    ):
        raise KnowledgeError("教材原始许可与登记不一致")
    for item in plan["sources"]:
        try:
            path = f"modules/{item['module_id']}/index.cnxml"
            raw = acquire(path, item["expected_cnxml_sha256"])
            payload = parse_module(raw, item, revision)
            digest = hashlib.sha256(payload["content"].encode()).hexdigest()
            if digest != item["expected_text_sha256"]:
                raise KnowledgeError("教材提取文本与登记不一致")
            target = documents / (item["module_id"] + ".json")
            target.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())
            report["sources"].append({"module_id": item["module_id"], "text_sha256": digest})
        except Exception as exc:
            report["errors"].append({"module_id": item["module_id"], "error": str(exc)})
    report["ready"] = not report["errors"] and len(report["sources"]) == len(plan["sources"])
    (output / "collection.json").write_bytes(
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "research/cell-biology-expanded-v2/foundation-sources.json",
    )
    parser.add_argument("--source-cache", type=Path, help="可选原始文件缓存；仍逐文件核对摘要")
    args = parser.parse_args()
    report = collect(args.output, args.registry, args.source_cache)
    print(
        json.dumps(
            {
                "ready": report["ready"],
                "documents": len(report["sources"]),
                "errors": report["errors"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["ready"] else 1)
