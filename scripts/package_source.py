"""按白名单打包当前主线，附带 SHA-256 清单，不包含文献、凭据和历史实现。"""

import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
# 当前源码、工具、冻结题库和带许可的复现样例都应随包分发。
SINGLES = [
    "app.py",
    "start.bat",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    "README.md",
    "AGENTS.md",
    "THIRD_PARTY_NOTICES.md",
    ".github/workflows/biokb.yml",
]


def package(output: Path | None = None) -> Path:
    target = output or ROOT / "output/distributions" / (
        "BioKB-source-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
    )
    candidates = [ROOT / name for name in SINGLES]
    candidates.extend(path for path in (ROOT / "docs").rglob("*") if path.is_file())
    for name in ("src/biokb", "tests/biokb", "deploy", "scripts", "questionbank", "examples"):
        candidates.extend(path for path in (ROOT / name).rglob("*") if path.is_file())
    manifest = {}
    unbundled = set()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(set(candidates)):
            if not path.is_file():
                raise FileNotFoundError(f"源码包缺少必需文件：{path.relative_to(ROOT)}")
            relative = path.relative_to(ROOT)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            name = relative.as_posix()
            payload = path.read_bytes()
            if name.endswith(".md"):
                for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", payload.decode("utf-8-sig")):
                    parsed = urlsplit(link.strip("<>"))
                    if parsed.scheme or not parsed.path:
                        continue
                    target_path = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), unquote(parsed.path))
                    )
                    if target_path.startswith(
                        ("output/", "data/", "sci-illust-system/", "docs/archive/")
                    ):
                        unbundled.add(target_path)
            manifest[name] = hashlib.sha256(payload).hexdigest()
            bundle.writestr(name, payload)
        external = json.dumps(
            {
                "scope": "只登记未随源码包分发的历史实验、正式语料或归档链接；不表示这些文件已验证",
                "paths": sorted(unbundled),
            },
            ensure_ascii=False,
            indent=2,
        ).encode()
        bundle.writestr("UNBUNDLED_ASSETS.json", external)
        manifest["UNBUNDLED_ASSETS.json"] = hashlib.sha256(external).hexdigest()
        bundle.writestr("SOURCE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(package(parser.parse_args().output))
