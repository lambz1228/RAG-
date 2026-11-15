"""检查源码包清单、路径边界和主线 Markdown 的本地链接。"""

import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


def verify(path: Path) -> dict:
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read("SOURCE_MANIFEST.json"))
        assert set(manifest) == names - {"SOURCE_MANIFEST.json"}, "清单与文件不一致"
        errors = []
        for name, expected in manifest.items():
            if name.startswith("/") or ".." in name.split("/"):
                errors.append(f"越界路径：{name}")
            if any(
                part in {"data", ".venv", "archive", "sci-illust-system", "__pycache__"}
                for part in name.split("/")
            ):
                errors.append(f"不允许的目录：{name}")
            if (Path(name).name.startswith(".env") and name != ".env.example") or Path(
                name
            ).suffix in {".db", ".sqlite3", ".parquet", ".pyc"}:
                errors.append(f"不允许的文件：{name}")
            raw = bundle.read(name)
            if hashlib.sha256(raw).hexdigest() != expected:
                errors.append(f"摘要不一致：{name}")
            if name.endswith(".md"):
                for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", raw.decode("utf-8-sig")):
                    link = link.strip("<>")
                    parsed = urlsplit(link)
                    if parsed.scheme or not parsed.path:
                        continue
                    target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), unquote(parsed.path))
                    )
                    if target not in names:
                        errors.append(f"失效链接：{name} → {target}")
        return {
            "passed": not errors,
            "files": len(manifest),
            "zip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "errors": errors,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.path)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    raise SystemExit(0 if report["passed"] else 1)
