"""核对重构前已登记的原始数据 SHA-256，不修改被核对文件。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(baseline: Path, output: Path) -> dict:
    originals = json.loads(baseline.read_text(encoding="utf-8-sig"))
    changed, missing = [], []
    for relative, expected in originals.items():
        path = (ROOT / relative.replace("\\", "/")).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("基线中存在工作区外路径")
        if not path.is_file():
            missing.append(relative)
            continue
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            changed.append(relative)
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total": len(originals),
        "unchanged": len(originals) - len(changed) - len(missing),
        "changed": changed,
        "missing": missing,
        "passed": not changed and not missing,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / "output/refactor/data_preservation_before.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output/refactor/data_preservation_after.json"
    )
    args = parser.parse_args()
    report = verify(args.baseline, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
