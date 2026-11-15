"""按白名单打包当前主线，附带 SHA-256 清单，不包含文献、凭据和历史实现。"""

import argparse
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_DOCS = [
    "架构设计.md",
    "API说明.md",
    "开发说明.md",
    "部署文档.md",
    "实验设计.md",
    "重构迁移说明.md",
    "验证记录.md",
    "优化实施记录.md",
    "文件清理记录.md",
    "2.1优化验收.md",
    "双语工作台验收.md",
    "科研证据整改验收.md",
    "科研质量验收报告.md",
    "科研质量优化计划.md",
    "文献扩充记录.md",
    "历史文献扩充记录.md",
    "缺口文献扩充记录.md",
]
SINGLES = [
    "app.py",
    "gui.py",
    "start.bat",
    "start-dev.bat",
    "start-expanded.bat",
    "build.bat",
    "build_gui.spec",
    "pack.bat",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "使用说明书.md",
    "项目介绍.md",
    "SETUP_GUIDE.md",
    "AGENTS.md",
    ".github/workflows/biokb.yml",
    "scripts/package_source.py",
    "scripts/verify_package.py",
    "scripts/biokb.py",
    "scripts/health_check.ps1",
    "scripts/verify_browser.py",
    "scripts/verify_workspace.py",
    "scripts/verify_preservation.py",
    "scripts/verify_runtime.py",
    "scripts/map_legacy_sources.py",
    "scripts/README.md",
    "scripts/check_models.py",
    "scripts/fetch_open_fulltexts.py",
    "scripts/expand_open_corpus.py",
    "scripts/collect_seeded_corpus.py",
    "scripts/discover_gap_candidates.py",
    "scripts/prepare_gap_retry.py",
    "scripts/fetch_pubmed_metadata.py",
    "scripts/curate_open_corpus.py",
    "scripts/import_expanded_corpus.py",
    "scripts/enrich_corpus_authors.py",
    "scripts/verify_expanded_index.py",
    "scripts/verify_expanded_api.py",
    "scripts/verify_pubmed_snapshot.py",
    "scripts/prepare_research_dataset.py",
    "scripts/run_research_experiments.py",
    "scripts/prepare_answer_review.py",
    "scripts/audit_research_corpus.py",
    "scripts/audit_research_quality.py",
    "scripts/derive_paper_metrics.py",
    "scripts/replay_research_cases.py",
    "scripts/verify_research.py",
]


def package(output: Path | None = None) -> Path:
    target = output or ROOT / "output/distributions" / (
        "BioKB-source-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
    )
    candidates = [ROOT / name for name in SINGLES] + [ROOT / "docs" / name for name in MAIN_DOCS]
    for name in ("src/biokb", "tests/biokb", "deploy"):
        candidates.extend(path for path in (ROOT / name).rglob("*") if path.is_file())
    manifest = {}
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
            manifest[name] = hashlib.sha256(payload).hexdigest()
            bundle.writestr(name, payload)
        bundle.writestr("SOURCE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(package(parser.parse_args().output))
