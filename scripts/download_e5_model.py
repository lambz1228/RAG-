"""下载固定修订的官方多语言E5 ONNX模型，核对上游LFS摘要并生成本地清单。"""

import argparse
import hashlib
import json
from pathlib import Path

import requests

REPOSITORY = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = {
    "model.onnx": "onnx/model_qint8_avx512_vnni.onnx",
    "tokenizer.json": "onnx/tokenizer.json",
    "config.json": "onnx/config.json",
    "tokenizer_config.json": "onnx/tokenizer_config.json",
    "README.md": "README.md",
}


def download(output):
    output.mkdir(parents=True, exist_ok=False)
    records = {}
    with requests.Session() as session:
        trees = []
        for folder in ("", "/onnx"):
            response = session.get(
                f"https://huggingface.co/api/models/{REPOSITORY}/tree/{REVISION}{folder}",
                timeout=40,
            )
            response.raise_for_status()
            trees.extend(response.json())
        by_path = {row["path"]: row for row in trees}
        for name, remote in FILES.items():
            metadata = by_path[remote]
            target = output / name
            partial = output / (name + ".part")
            sha, gitsha = hashlib.sha256(), hashlib.sha1()
            gitsha.update(b"blob " + str(metadata["size"]).encode() + b"\0")
            count = 0
            with session.get(
                f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{remote}?download=true",
                stream=True,
                timeout=(20, 120),
            ) as response:
                response.raise_for_status()
                with partial.open("xb") as handle:
                    for block in response.iter_content(1024 * 1024):
                        handle.write(block)
                        sha.update(block)
                        gitsha.update(block)
                        count += len(block)
            expected = metadata.get("lfs", {}).get("oid")
            if (
                count != metadata["size"]
                or (expected and sha.hexdigest() != expected)
                or (not expected and gitsha.hexdigest() != metadata["oid"])
            ):
                raise ValueError("模型文件大小或上游摘要不一致：" + remote)
            partial.replace(target)
            records[name] = {"sha256": sha.hexdigest(), "bytes": count, "source_path": remote}
            print(json.dumps({"file": name, "bytes": count, "verified": True}), flush=True)
    manifest = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "revision": REVISION,
        "format": "onnx-qint8-avx512-vnni",
        "files": records,
    }
    (output / "MODEL_MANIFEST.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    download(parser.parse_args().output)
