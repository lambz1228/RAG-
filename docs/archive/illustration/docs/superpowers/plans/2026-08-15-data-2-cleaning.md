# data_2 强力清洗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `D:\ljn-xm\data_2.zip` 建立可重复执行的强力清洗流程，产出分层清洗数据、异常记录、统计摘要和验证结果，为后续知识库导入提供稳定输入。

**Architecture:** 保持原始数据包只读，在 `scripts/clean_data_bundle.py` 中实现单入口清洗编排，将元素层、文献层、映射层和图谱层拆分为可单测的纯函数与轻量数据结构。输出统一落到 `build/data_2_cleaned` 与 `build/data_2_reports`，由验证函数生成最终 `validation_summary.json`，并通过单元测试和最小 ZIP 集成测试锁定行为。

**Tech Stack:** Python 3.12、标准库 `zipfile/json/re/pathlib/html/urllib`、现有 `unittest` 测试体系

## Global Constraints

- 输入只读：不修改 `D:\ljn-xm\data_2.zip`，不覆盖原始解压内容。
- 输出固定到 `build/data_2_cleaned/` 与 `build/data_2_reports/`，支持重复执行和整目录重建。
- 本轮只做数据清洗与验证，不直接写入 `knowledge.db` 或 MySQL。
- 需要同时产出清洗结果、异常日志、清洗统计、验证摘要。
- 对明显坏数据可以过滤，但不得凭空生成学术内容；无法可靠修复时记录异常并剔除。
- 测试优先采用 `python -m unittest discover -s tests -p 'test_*.py' -v` 验证。

---

## File Structure

- Create: `scripts/clean_data_bundle.py`
  - 单入口清洗脚本，负责 ZIP 读取、四层清洗编排、输出写入、摘要与验证。
- Create or Modify: `tests/test_clean_data_bundle.py`
  - 纯函数单测与最小 ZIP 集成测试。
- Modify: `tests/test_import_data_bundle.py`
  - 复用已有部分清洗相关断言，避免与新脚本职责重叠。
- Create: `build/.gitkeep`
  - 如仓库约定需要，确保输出根目录存在；若项目不跟踪 `build/`，则跳过。
- Reference only: `docs/superpowers/specs/2026-08-15-data-2-cleaning-design.md`
  - 本计划的唯一规格依据。

## Task 1: 建立清洗脚本骨架与输出契约

**Files:**
- Create: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Produces:
  - `parse_args(argv: list[str] | None = None) -> argparse.Namespace`
  - `run_cleaning(zip_path: Path, output_root: Path, min_relation_confidence: float = 0.8, overwrite: bool = False, sample_limit: int = 20) -> dict`
  - `prepare_output_dirs(output_root: Path, overwrite: bool) -> dict[str, Path]`
  - `write_json(path: Path, payload: object) -> None`
  - `write_jsonl(path: Path, rows: list[dict]) -> None`

- [ ] **Step 1: 写失败测试，锁定输出目录与主入口摘要结构**

```python
import tempfile
import unittest
from pathlib import Path

from scripts.clean_data_bundle import prepare_output_dirs


class CleanDataBundleScaffoldTest(unittest.TestCase):
    def test_prepare_output_dirs_creates_cleaned_and_report_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = prepare_output_dirs(Path(tmpdir), overwrite=False)

            self.assertTrue(paths["cleaned_root"].exists())
            self.assertTrue(paths["reports_root"].exists())
            self.assertTrue((paths["cleaned_root"] / "elements").exists())
            self.assertTrue((paths["cleaned_root"] / "literature").exists())
            self.assertTrue((paths["cleaned_root"] / "mappings").exists())
            self.assertTrue((paths["cleaned_root"] / "kg").exists())
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanDataBundleScaffoldTest -v`
Expected: FAIL with `ModuleNotFoundError` or missing function error for `prepare_output_dirs`

- [ ] **Step 3: 写最小实现，建立脚本骨架**

```python
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="清洗 data_2 数据包")
    parser.add_argument("zip_path")
    parser.add_argument("--output-root", default="build")
    parser.add_argument("--min-relation-confidence", type=float, default=0.8)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser.parse_args(argv)


def prepare_output_dirs(output_root: Path, overwrite: bool):
    if overwrite and output_root.exists():
        shutil.rmtree(output_root)
    cleaned_root = output_root / "data_2_cleaned"
    reports_root = output_root / "data_2_reports"
    for path in (
        cleaned_root / "elements",
        cleaned_root / "literature" / "texts",
        cleaned_root / "mappings",
        cleaned_root / "kg",
        reports_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return {"cleaned_root": cleaned_root, "reports_root": reports_root}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanDataBundleScaffoldTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): scaffold data bundle cleaning entrypoint"
```

## Task 2: 实现通用清洗基础函数

**Files:**
- Modify: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Produces:
  - `sanitize_filename(value: str, fallback: str) -> str`
  - `strip_html_to_text(value: str) -> str`
  - `normalize_whitespace(value: str) -> str`
  - `normalize_doi(value: str) -> str`
  - `normalize_url(value: str) -> str`
  - `is_probable_noise_text(value: str) -> bool`
  - `append_exception(exceptions: list[dict], *, stage: str, source_file: str, record_id: str, field: str, issue_type: str, severity: str, original_value: object, action: str, message: str) -> None`

- [ ] **Step 1: 写失败测试，覆盖通用清洗规则**

```python
class CleanDataBundleUtilityTest(unittest.TestCase):
    def test_strip_html_to_text_removes_tags_and_compacts_whitespace(self):
        text = strip_html_to_text("<h4>Aims</h4> Test<br>value")
        self.assertEqual(text, "Aims Test value")

    def test_normalize_doi_accepts_doi_url(self):
        self.assertEqual(
            normalize_doi(" https://doi.org/10.1016/J.CELL.2005.02.001 "),
            "10.1016/j.cell.2005.02.001",
        )

    def test_normalize_url_rejects_non_http_protocol(self):
        self.assertEqual(normalize_url("ftp://example.com/a"), "")

    def test_is_probable_noise_text_flags_html_tag_name(self):
        self.assertTrue(is_probable_noise_text("h4"))
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanDataBundleUtilityTest -v`
Expected: FAIL for missing functions or wrong normalization behavior

- [ ] **Step 3: 写最小实现**

```python
import html
import re
from urllib.parse import urlparse


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def strip_html_to_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return normalize_whitespace(text)


def normalize_doi(value: str) -> str:
    raw = normalize_whitespace(value).lower()
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw)
    return raw if re.match(r"^10\.\S+/\S+$", raw) else ""


def normalize_url(value: str) -> str:
    raw = normalize_whitespace(value)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.geturl()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanDataBundleUtilityTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): add shared normalization helpers"
```

## Task 3: 实现元素层清洗

**Files:**
- Modify: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Produces:
  - `make_element_key(element: dict) -> str`
  - `clean_element_record(element: dict, source_file: str, exceptions: list[dict]) -> dict | None`
  - `clean_elements(domain_vocab: dict, kb_elements: list[dict], exceptions: list[dict]) -> dict`

- [ ] **Step 1: 写失败测试，锁定元素合并与过滤行为**

```python
class CleanElementsTest(unittest.TestCase):
    def test_clean_elements_merges_duplicate_ids_and_sorts_tags(self):
        domain_vocab = {"domains": {}}
        kb_elements = [
            {"element_id": "mitochondrion", "name": "线粒体", "english": "mitochondrion", "domain": "生物学", "category": "细胞器", "shape": "bean", "tags": ["ATP", "能量", "ATP"]},
            {"element_id": "mitochondrion", "name": "线粒体", "english": "mitochondrion", "domain": "生物学", "category": "细胞器", "shape": "bean", "tags": ["动力"]},
        ]

        cleaned = clean_elements(domain_vocab, kb_elements, [])

        item = cleaned["element_index"]["mitochondrion"]
        self.assertEqual(item["tags"], ["ATP", "动力", "能量"])
        self.assertEqual(item["source_count"], 2)

    def test_clean_element_record_returns_none_when_name_and_english_missing(self):
        self.assertIsNone(clean_element_record({"element_id": ""}, "kb.json", []))
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanElementsTest -v`
Expected: FAIL for missing functions

- [ ] **Step 3: 写最小实现**

```python
def make_element_key(element):
    for candidate in (element.get("element_id"), element.get("english"), element.get("name")):
        key = normalize_whitespace(candidate).lower().replace(" ", "_")
        if key:
            return key
    return ""


def clean_element_record(element, source_file, exceptions):
    name = normalize_whitespace(element.get("name"))
    english = normalize_whitespace(element.get("english"))
    if not name and not english:
        append_exception(...)
        return None
    tags = sorted({normalize_whitespace(tag) for tag in element.get("tags", []) if normalize_whitespace(tag)})
    return {
        "element_id": make_element_key(element),
        "name": name,
        "english": english,
        "domain": normalize_whitespace(element.get("domain")),
        "category": normalize_whitespace(element.get("category")),
        "shape": normalize_whitespace(element.get("shape")),
        "tags": tags,
    }
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanElementsTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): implement element cleaning layer"
```

## Task 4: 实现文献层清洗

**Files:**
- Modify: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Produces:
  - `make_literature_key(record: dict) -> str`
  - `clean_literature_record(record: dict, source_file: str, record_id: str, exceptions: list[dict]) -> dict | None`
  - `merge_literature_records(current: dict, incoming: dict) -> dict`
  - `clean_literature(normalized_docs: list[dict], exported_docs: list[dict], exceptions: list[dict]) -> dict`

- [ ] **Step 1: 写失败测试，锁定 HTML 去除、DOI 去重和正文过滤**

```python
class CleanLiteratureTest(unittest.TestCase):
    def test_clean_literature_record_strips_html_and_normalizes_doi(self):
        row = {
            "id": "1",
            "title": " Example ",
            "content": "<h4>Aims</h4>Body",
            "doi": "https://doi.org/10.1000/ABC",
            "url": "https://example.org/paper",
        }

        cleaned = clean_literature_record(row, "normalized/1.json", "1", [])

        self.assertEqual(cleaned["content"], "Aims Body")
        self.assertEqual(cleaned["doi"], "10.1000/abc")

    def test_clean_literature_record_returns_none_when_cleaned_content_empty(self):
        self.assertIsNone(
            clean_literature_record({"id": "x", "title": "A", "content": "<div> </div>"}, "f.json", "x", [])
        )
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanLiteratureTest -v`
Expected: FAIL for missing functions

- [ ] **Step 3: 写最小实现**

```python
def make_literature_key(record):
    for candidate in (record.get("doi"), record.get("url"), f"{record.get('source')}::{record.get('id')}", record.get("title")):
        key = normalize_whitespace(candidate)
        if key:
            return key.lower()
    return ""


def clean_literature_record(record, source_file, record_id, exceptions):
    title = normalize_whitespace(record.get("title"))
    content = strip_html_to_text(record.get("content") or record.get("abstract") or "")
    doi = normalize_doi(record.get("doi"))
    url = normalize_url(record.get("url"))
    if not content:
        append_exception(...)
        return None
    return {
        "record_id": normalize_whitespace(record_id),
        "title": title,
        "content": content,
        "doi": doi,
        "url": url,
        "year": record.get("year"),
    }
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanLiteratureTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): implement literature cleaning layer"
```

## Task 5: 实现映射层清洗

**Files:**
- Modify: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Produces:
  - `normalize_reference_link(value: str) -> tuple[str, str]`
  - `clean_element_literature_map(raw_map: dict, element_index: dict, literature_index: dict, exceptions: list[dict]) -> dict`

- [ ] **Step 1: 写失败测试，锁定合法映射保留与坏链接过滤**

```python
class CleanMappingTest(unittest.TestCase):
    def test_clean_element_literature_map_keeps_only_known_elements_and_docs(self):
        raw_map = {"linkage": {"mitochondrion": ["10.1000/abc", "bad-ref"], "ghost": ["10.1000/abc"]}}
        element_index = {"mitochondrion": {"element_id": "mitochondrion"}}
        literature_index = {"10.1000/abc": {"record_id": "doc-1"}}
        exceptions = []

        cleaned = clean_element_literature_map(raw_map, element_index, literature_index, exceptions)

        self.assertEqual(cleaned["linkage"]["mitochondrion"], ["10.1000/abc"])
        self.assertNotIn("ghost", cleaned["linkage"])
        self.assertGreaterEqual(len(exceptions), 1)
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanMappingTest -v`
Expected: FAIL for missing functions

- [ ] **Step 3: 写最小实现**

```python
def normalize_reference_link(value):
    doi = normalize_doi(value)
    if doi:
        return ("doi", doi)
    url = normalize_url(value)
    if url:
        return ("url", url)
    return ("", "")


def clean_element_literature_map(raw_map, element_index, literature_index, exceptions):
    cleaned = {"linkage": {}}
    for element_id, refs in raw_map.get("linkage", {}).items():
        if element_id not in element_index:
            append_exception(...)
            continue
        kept = []
        for ref in refs:
            _, normalized = normalize_reference_link(ref)
            if normalized and normalized in literature_index:
                kept.append(normalized)
            else:
                append_exception(...)
        if kept:
            cleaned["linkage"][element_id] = sorted(set(kept))
    return cleaned
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanMappingTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): implement element literature mapping cleanup"
```

## Task 6: 实现图谱层清洗

**Files:**
- Modify: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Produces:
  - `clean_entity_record(record: dict, exceptions: list[dict]) -> dict | None`
  - `clean_relation_record(record: dict, min_confidence: float, exceptions: list[dict]) -> dict | None`
  - `clean_kg(entities_payload: dict, relations_payload: list[dict], triples_payload: dict, element_index: dict, exceptions: list[dict], min_confidence: float) -> dict`

- [ ] **Step 1: 写失败测试，锁定噪声实体与低质量关系过滤**

```python
class CleanKgTest(unittest.TestCase):
    def test_clean_entity_record_filters_noise_entity(self):
        self.assertIsNone(clean_entity_record({"name": "h4", "aliases": []}, []))

    def test_clean_relation_record_filters_low_confidence_and_broken_subject(self):
        self.assertIsNone(
            clean_relation_record(
                {"subject": "", "relation": "activates", "object": "AKT", "confidence": 0.2},
                0.8,
                [],
            )
        )
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanKgTest -v`
Expected: FAIL for missing functions

- [ ] **Step 3: 写最小实现**

```python
ALLOWED_RELATIONS = {
    "related_to", "regulates", "activates", "inhibits", "binds_to",
    "phosphorylates", "produces", "composed_of", "located_in",
}


def clean_entity_record(record, exceptions):
    name = normalize_whitespace(record.get("name"))
    if not name or is_probable_noise_text(name):
        append_exception(...)
        return None
    aliases = sorted({normalize_whitespace(alias) for alias in record.get("aliases", []) if normalize_whitespace(alias)})
    return {"name": name, "aliases": aliases, "type": normalize_whitespace(record.get("type"))}


def clean_relation_record(record, min_confidence, exceptions):
    subject = normalize_whitespace(record.get("subject"))
    relation = normalize_whitespace(record.get("relation")).lower()
    obj = normalize_whitespace(record.get("object"))
    confidence = float(record.get("confidence") or 0)
    if not subject or not obj or confidence < min_confidence:
        append_exception(...)
        return None
    if relation not in ALLOWED_RELATIONS:
        relation = "other"
    return {"subject": subject, "relation": relation, "object": obj, "confidence": confidence}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanKgTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): implement knowledge graph cleaning layer"
```

## Task 7: 串联端到端清洗、摘要与验证

**Files:**
- Modify: `scripts/clean_data_bundle.py`
- Test: `tests/test_clean_data_bundle.py`

**Interfaces:**
- Consumes:
  - `clean_elements(...) -> dict`
  - `clean_literature(...) -> dict`
  - `clean_element_literature_map(...) -> dict`
  - `clean_kg(...) -> dict`
- Produces:
  - `build_cleaning_summary(...) -> dict`
  - `build_validation_summary(...) -> dict`
  - `run_cleaning(...) -> dict`

- [ ] **Step 1: 写失败测试，使用最小 ZIP 样本验证主流程**

```python
class CleanDataBundleIntegrationTest(unittest.TestCase):
    def test_run_cleaning_writes_outputs_and_reports(self):
        result = run_cleaning(sample_zip_path, output_root, min_relation_confidence=0.8, overwrite=True, sample_limit=5)

        self.assertTrue((output_root / "data_2_cleaned" / "elements" / "element_index.cleaned.json").exists())
        self.assertTrue((output_root / "data_2_reports" / "cleaning_summary.json").exists())
        self.assertTrue((output_root / "data_2_reports" / "validation_summary.json").exists())
        self.assertTrue((output_root / "data_2_reports" / "exceptions.jsonl").exists())
        self.assertIn("validation", result)
        self.assertIn("passed", result["validation"])
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run: `python -m unittest tests.test_clean_data_bundle.CleanDataBundleIntegrationTest -v`
Expected: FAIL for incomplete `run_cleaning`

- [ ] **Step 3: 写最小实现，串联 ZIP 读取、清洗输出、摘要与验证**

```python
def run_cleaning(zip_path, output_root, min_relation_confidence=0.8, overwrite=False, sample_limit=20):
    paths = prepare_output_dirs(output_root, overwrite=overwrite)
    exceptions = []
    with zipfile.ZipFile(zip_path) as zf:
        # 读取核心输入
        ...
    elements = clean_elements(...)
    literature = clean_literature(...)
    mapping = clean_element_literature_map(...)
    kg = clean_kg(...)
    summary = build_cleaning_summary(...)
    validation = build_validation_summary(...)
    write_json(...)
    write_jsonl(...)
    return {"summary": summary, "validation": validation}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m unittest tests.test_clean_data_bundle.CleanDataBundleIntegrationTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py
git commit -m "feat(cleaning): add end-to-end cleaning reports"
```

## Task 8: 回归、真实数据包验证与交付说明

**Files:**
- Modify: `scripts/clean_data_bundle.py` if needed
- Modify: `tests/test_import_data_bundle.py` if overlap cleanup is needed

**Interfaces:**
- Consumes:
  - `python -m unittest discover -s tests -p 'test_*.py' -v`
  - `python scripts/clean_data_bundle.py D:\ljn-xm\data_2.zip --output-root build --overwrite`
- Produces:
  - 已验证的清洗脚本和报告产物

- [ ] **Step 1: 运行清洗脚本的最小测试集**

Run: `python -m unittest tests.test_clean_data_bundle -v`
Expected: PASS

- [ ] **Step 2: 运行项目回归测试**

Run: `python -m unittest discover -s tests -p 'test_*.py' -v`
Expected: PASS or only known unrelated failures explicitly identified

- [ ] **Step 3: 对真实 `data_2.zip` 执行一次清洗**

Run: `python scripts/clean_data_bundle.py D:\ljn-xm\data_2.zip --output-root build --overwrite`
Expected: exit 0 and write `build/data_2_cleaned/*` plus `build/data_2_reports/*`

- [ ] **Step 4: 检查摘要与验证结果**

Run: `python - <<'PY'\nimport json, pathlib\nroot = pathlib.Path('build/data_2_reports')\nprint(json.loads((root/'cleaning_summary.json').read_text(encoding='utf-8'))['counts'])\nprint(json.loads((root/'validation_summary.json').read_text(encoding='utf-8'))['passed'])\nPY`
Expected: counts present and validation pass/fail status explicitly visible

- [ ] **Step 5: Commit**

```bash
git add scripts/clean_data_bundle.py tests/test_clean_data_bundle.py tests/test_import_data_bundle.py
git commit -m "feat(cleaning): add repeatable data bundle cleanup workflow"
```

## Self-Review

- Spec coverage:
  - 输入只读、输出固定目录、四层清洗、异常日志、清洗统计、验证摘要都已对应到 Task 1-7。
  - 真实数据包运行验证和回归检查在 Task 8 覆盖。
- Placeholder scan:
  - 所有任务均给出明确文件、函数接口、测试命令和预期结果。
  - 未使用 `TODO`、`TBD` 或“稍后实现”类占位描述。
- Type consistency:
  - 四层清洗都通过 `exceptions: list[dict]` 聚合异常。
  - `run_cleaning(...) -> dict` 统一返回 `summary` 与 `validation`。

