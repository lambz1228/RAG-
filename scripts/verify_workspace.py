"""在临时数据库中验证双语工作台；可从正式库只读复制一份进行界面截图。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from verify_browser import ROOT, Settings, running_app


def navigate(page, view: str) -> None:
    """通过可见的导航按钮切换，移动端先展开抽屉。"""
    target = page.locator(f'[data-view="{view}"]')
    if not target.is_visible():
        page.locator("#toggle-sidebar").click()
    target.click()


def check_translations(page) -> None:
    """检查当前页面全部已标记文案及静态调用使用的翻译键。"""
    keys = set()
    for source in (ROOT / "src/biokb/web/static").glob("*.js"):
        keys.update(re.findall(r'\b(?:msg|t)\("([^"\n]+)"', source.read_text(encoding="utf-8")))
    missing = page.evaluate(
        """async keys => {
          const {english} = await import('/static/messages.js');
          for (const node of document.querySelectorAll('*')) {
            for (const attr of node.attributes) {
              if (['data-i18n', 'data-i18n-placeholder', 'data-i18n-title',
                   'data-i18n-aria-label'].includes(attr.name)) keys.push(attr.value);
            }
          }
          return [...new Set(keys)].filter(key => key !== '@date' && !(key in english));
        }""",
        sorted(keys),
    )
    assert not missing, f"缺少英文文案：{missing}"


def verify(output: Path, snapshot_data: Path | None = None) -> dict:
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": [], "page_errors": [], "console_errors": []}
    try:
        with tempfile.TemporaryDirectory(prefix="biokb-workspace-") as temporary:
            settings = Settings(data_dir=Path(temporary), chunk_size=200, chunk_overlap=30)
            with running_app(settings) as (url, services), sync_playwright() as engine:
                browser = engine.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                page.set_default_timeout(10000)
                report["browser"] = browser.version
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                page.on("console", lambda message: report["console_errors"].append(message.text)
                        if message.type == "error" else None)
                requests = []
                page.on("request", lambda request: requests.append(request.url)
                        if "/api/" in request.url else None)
                for number in range(45):
                    services.ingestion.ingest(
                        f"workspace-{number:02d}.txt",
                        (f"测试资料 {number:02d}：线粒体参与 ATP 能量代谢。" * 12).encode(),
                        {"title": f"线粒体文献 {number:02d}", "content_type": "abstract"},
                    )
                for kind in ("fulltext", "knowledge_note", "reference_stub"):
                    services.ingestion.ingest(
                        f"{kind}.txt", (f"类型 {kind} 的测试资料：细胞参与能量代谢。" * 10).encode(),
                        {"title": f"类型测试 {kind}", "content_type": kind},
                    )
                page.goto(url)
                expect(page.locator("#stats .stat-number").first).to_have_text("48")
                expect(page.locator(".tree-group").first.locator(".tree-document")).to_have_count(40)
                page.get_by_role("button", name="加载更多文献").click()
                expect(page.locator(".tree-group").first.locator(".tree-document")).to_have_count(45)
                expect(page.locator(".tree-more")).to_have_count(0)
                for group in page.locator(".tree-group").all()[1:]:
                    group.locator("summary").click()
                    expect(group.locator(".tree-document")).to_have_count(1)
                report["checks"].append("四类文献树按需加载、40 条分页及加载更多")

                page.locator("#tree-query").fill("线粒体文献 07")
                expect(page.locator("#document-tree .tree-document")).to_have_count(1)
                page.locator("#document-tree .tree-document").click()
                expect(page.locator("#reader-title")).to_have_text("线粒体文献 07")
                original_text = page.locator("#reader-body .document-text").all_text_contents()
                expect(page.locator("#tab-reader")).to_have_text("线粒体文献 07")
                page.locator("#language-select").select_option("en")
                expect(page.locator("#reader-title")).to_have_text("线粒体文献 07")
                assert page.locator("#reader-body .document-text").all_text_contents() == original_text
                page.locator("#tree-query").fill("不存在的标题")
                expect(page.locator("#document-tree")).to_contain_text("No matching documents")
                page.locator("#tree-query").fill("")
                expect(page.locator(".tree-group").first.locator(".tree-document")).to_have_count(40)
                report["checks"].append("文献标题搜索、空结果、原文阅读及正文不被翻译")

                navigate(page, "overview")
                expect(page.locator("#recent-documents tbody tr")).to_have_count(5)
                recent = page.locator("#recent-documents .text-button").all_text_contents()
                page.wait_for_load_state("networkidle")
                before = len(requests)
                page.locator("#language-select").select_option("zh-CN")
                expect(page.locator("#tab-overview")).to_have_text("知识库概览")
                expect(page.locator("#recent-documents tbody tr")).to_have_count(5)
                page.locator("#language-select").select_option("en")
                expect(page.locator("#tab-overview")).to_have_text("Overview")
                assert page.locator("#recent-documents .text-button").all_text_contents() == recent
                assert len(requests) == before, "切换语言不应请求 API"
                check_translations(page)
                report["checks"].append("中英文往返切换保留最近入库表格且不请求 API")

                navigate(page, "search")
                page.locator("#search-query").fill("线粒体 ATP")
                page.locator("#search-form").get_by_role("button", name="Search knowledge base").click()
                expect(page.locator("#search-results .result-card").first).to_be_visible()
                snippets = page.locator("#search-results .result-text").all_text_contents()
                page.locator("#language-select").select_option("zh-CN")
                expect(page.locator("#search-query")).to_have_value("线粒体 ATP")
                assert page.locator("#search-results .result-text").all_text_contents() == snippets
                navigate(page, "ask")
                question = "线粒体 ATP 的作用是什么？"
                page.locator("#ask-query").fill(question)
                page.locator("#ask-form").get_by_role("button", name="依据文献回答").click()
                expect(page.locator("#answer-result .answer-text")).to_be_visible(timeout=20000)
                answer = page.locator("#answer-result .answer-text").inner_text()
                page.locator("#ask-query").fill("保留尚未提交的问题")
                page.wait_for_load_state("networkidle")
                before = len(requests)
                page.locator("#language-select").select_option("en")
                expect(page.locator("#ask-query")).to_have_value("保留尚未提交的问题")
                assert page.locator("#answer-result .answer-text").inner_text() == answer
                assert len(requests) == before
                assert "{count}" not in page.locator("#page-outline").inner_text()
                page.locator("#answer-result .citation-link").first.click()
                expect(page.locator('#answer-result [data-evidence-number="1"]')).to_be_focused()
                page.locator('#answer-result [data-evidence-number="1"]').get_by_role(
                    "button", name="Open document & passage"
                ).click()
                expect(page.locator("#document-dialog .selected-evidence")).to_be_visible()
                dialog_text = page.locator("#document-dialog .document-text").all_text_contents()
                page.locator("#document-dialog .language-select").select_option("zh-CN")
                expect(page.locator("#language-select")).to_have_value("zh-CN")
                assert page.locator("#document-dialog .document-text").all_text_contents() == dialog_text
                page.locator("#close-document").click()
                report["checks"].append("双语检索与问答保留输入、证据、回答；引用定位和弹窗语言同步")

                page.locator("#tab-search").click()
                expect(page.locator("#search-query")).to_have_value("线粒体 ATP")
                page.locator("#tab-ask").click()
                expect(page.locator("#ask-query")).to_have_value("保留尚未提交的问题")
                page.locator("#tab-ask").press("Home")
                expect(page.locator("#tab-overview")).to_be_focused()
                page.locator("#tab-overview").press("End")
                expect(page.locator("#tab-ask")).to_be_focused()
                page.locator(".tab-item").filter(has=page.locator("#tab-ask")).locator(".tab-close").click()
                expect(page.locator("#tab-ask")).to_have_count(0)
                expect(page.locator("#tab-search")).to_have_attribute("aria-selected", "true")
                navigate(page, "ask")
                expect(page.locator("#ask-query")).to_have_value("保留尚未提交的问题")
                assert page.locator("#answer-result .answer-text").inner_text() == answer
                report["checks"].append("标签切换、键盘 Home/End、关闭与重开保留表单和结果")

                navigate(page, "documents")
                page.locator('#upload-form input[name="title"]').fill("保留标题")
                page.locator("#document-file").set_input_files(
                    {"name": "坏文件.pdf", "mimeType": "application/pdf", "buffer": b"not a PDF"}
                )
                page.locator("#language-select").select_option("en")
                expect(page.locator('#upload-form input[name="title"]')).to_have_value("保留标题")
                assert page.locator("#document-file").evaluate("node => node.files[0].name") == "坏文件.pdf"
                page.locator('#upload-form button[type="submit"]').click()
                expect(page.locator("#upload-status")).to_contain_text("failed", timeout=20000)
                expect(page.locator('#upload-form button[type="submit"]')).to_be_enabled()
                assert not re.search(r"[\u4e00-\u9fff]", page.locator("#upload-status").inner_text())
                check_translations(page)
                report["checks"].append("切换语言保留已选文件及标题；上传失败提示和按钮恢复为英文")

                page.reload()
                expect(page.locator("html")).to_have_attribute("lang", "en")
                expect(page.locator("#tab-documents")).to_have_text("Documents")
                navigate(page, "history")
                expect(page.locator("#history-list")).to_contain_text(question)
                check_translations(page)
                report["checks"].append("刷新保持语言选择、问答历史原始内容及英文日期")

                page.locator("#toggle-sidebar").click()
                expect(page.locator("#navigation-pane")).to_be_hidden()
                page.keyboard.press("Control+k")
                expect(page.locator("#tree-query")).to_be_focused()
                expect(page.locator("#toggle-sidebar")).to_have_attribute("aria-expanded", "true")
                page.locator("#toggle-inspector").click()
                expect(page.locator("#inspector")).to_be_hidden()
                page.locator("#toggle-inspector").click()
                expect(page.locator("#inspector")).to_be_visible()
                report["checks"].append("左右面板折叠及 Ctrl+K 展开文献搜索")

                page.locator("#document-tree .tree-document").first.click()
                expect(page.locator("#reader-body .document-text").first).to_be_visible()

                for width in (390, 760, 820, 1280, 1440):
                    page.set_viewport_size({"width": width, "height": 900})
                    for locale in ("zh-CN", "en"):
                        page.locator("#language-select").select_option(locale)
                        for view in ("overview", "documents", "search", "ask", "history", "evaluation", "settings", "reader"):
                            if view == "reader":
                                page.locator("#tab-reader").click()
                            else:
                                navigate(page, view)
                            expect(page.locator(f'[data-panel="{view}"]')).to_be_visible()
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (width, locale, view)
                            assert page.locator("main").evaluate("node => node.scrollWidth <= node.clientWidth + 1"), ("内容区溢出", width, locale, view)
                        check_translations(page)
                report["checks"].append("390/760/820/1280/1440px、中英文、八页面均无整页或内容区横向溢出")

                page.set_viewport_size({"width": 390, "height": 844})
                page.reload()
                expect(page.locator("#toggle-sidebar")).to_have_attribute("aria-expanded", "false")
                page.locator("#toggle-sidebar").click()
                expect(page.locator("#navigation-pane")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("#navigation-pane")).to_be_hidden()
                expect(page.locator("#toggle-sidebar")).to_be_focused()
                page.locator("#toggle-sidebar").click()
                page.locator("#navigation-shade").click(position={"x": 350, "y": 200})
                expect(page.locator("#navigation-pane")).to_be_hidden()
                navigate(page, "overview")
                page.screenshot(path=str(output / "mobile_en.png"))
                report["checks"].append("手机首次加载展开状态、Escape 关闭、遮罩关闭和导航后自动收起")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator("#language-select").select_option("zh-CN")
                navigate(page, "overview")
                page.screenshot(path=str(output / "overview_zh.png"))
                page.locator("#language-select").select_option("en")
                page.screenshot(path=str(output / "overview_en.png"))
                browser.close()

        if snapshot_data:
            report["snapshot"] = snapshot(output, snapshot_data)
        assert not report["page_errors"], report["page_errors"]
        assert not report["console_errors"], report["console_errors"]
        report["passed"] = True
        return report
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot(output: Path, data: Path) -> dict:
    """仅读取源数据库，应用启动和任务恢复均发生在临时副本。"""
    from playwright.sync_api import expect, sync_playwright

    source = data.resolve() / "knowledge.sqlite3"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="biokb-preview-") as temporary:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(Path(temporary) / source.name)) as copied:
                original.backup(copied)
        settings = replace(Settings.from_env(), data_dir=Path(temporary), api_token="")
        with running_app(settings) as (url, services), sync_playwright() as engine:
            browser = engine.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(url)
            stats = services.repository.stats()
            expect(page.locator("#stats .stat-number").first).to_have_text(str(stats["documents"]))
            expect(page.locator("#recent-documents tbody tr")).to_have_count(min(5, stats["documents"]))
            page.screenshot(path=str(output / "knowledge_zh.png"))
            page.locator("#language-select").select_option("en")
            page.screenshot(path=str(output / "knowledge_en.png"))
            page.locator("#document-tree .tree-document").first.click()
            expect(page.locator("#reader-body .document-text").first).to_be_visible()
            page.screenshot(path=str(output / "knowledge_reader.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            navigate(page, "overview")
            page.locator("#language-select").select_option("zh-CN")
            page.screenshot(path=str(output / "knowledge_mobile.png"))
            browser.close()
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after, "截图前后源数据库摘要变化"
    return {"stats": stats, "source_sha256": after, "read_only_copy": True}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/workspace")
    parser.add_argument("--snapshot-data", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.snapshot_data), ensure_ascii=False, indent=2))
