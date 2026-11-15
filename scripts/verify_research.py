"""用隔离语料验证论文目录、研究范围、相邻证据与双语页面。"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from verify_browser import Settings, running_app


def verify(output: Path) -> dict:
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": [], "page_errors": [], "console_errors": []}
    try:
        with tempfile.TemporaryDirectory(prefix="biokb-research-") as temporary:
            with (
                running_app(
                    Settings(data_dir=Path(temporary), chunk_size=200, chunk_overlap=30)
                ) as (url, services),
                sync_playwright() as engine,
            ):
                documents = []
                for name, pmid, kind in [
                    ("研究 A 摘要", "123", "abstract"),
                    ("研究 A 全文", "123", "fulltext"),
                    ("研究 B 摘要", "456", "abstract"),
                ]:
                    item = services.ingestion.ingest(
                        name + ".json",
                        json.dumps(
                            {
                                "content": (name + " EGFR ATP 证据研究材料。") * 40,
                                "title": name,
                                "pmid": pmid,
                                "content_type": kind,
                                "topic": "mitochondria",
                                "year": "2025",
                            }
                        ).encode(),
                    )
                    documents.append(item["document"]["id"])
                browser = engine.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                page.on(
                    "console",
                    lambda event: (
                        report["console_errors"].append(event.text)
                        if event.type == "error"
                        else None
                    ),
                )
                page.goto(url + "/#papers")
                expect(page.locator(".paper-card")).to_have_count(2)
                expect(page.locator("#papers-summary .stat-number").first).to_have_text("2")
                page.locator("#papers-fulltext").check()
                expect(page.locator(".paper-card")).to_have_count(1)
                page.locator("#papers-list").get_by_role("button", name="针对这篇论文提问").click()
                expect(page.locator("#ask-scope")).to_contain_text("研究 A 全文")
                page.locator("#ask-query").fill("EGFR ATP")
                page.locator("#ask-form").get_by_role("button", name="依据文献回答").click()
                expect(page.locator("#answer-result .answer-text")).to_be_visible(timeout=15000)
                titles = page.locator("#answer-result .result-card h3").all_text_contents()
                assert titles and all("研究 A" in title for title in titles), titles
                assert "研究 B" not in page.locator("#answer-result").inner_text()
                report["checks"].append("同一论文摘要与全文关联；限定论文问答排除其他研究")
                page.locator("#answer-result .result-card").first.get_by_role(
                    "button", name="查看文献与文本块"
                ).click()
                expect(page.locator("#document-dialog .selected-evidence")).to_be_visible()
                assert page.locator("#document-dialog .context-evidence").count() >= 1
                page.locator("#close-document").click()
                report["checks"].append("扩展证据可定位召回中心与相邻原文")
                page.locator("#language-select").select_option("en")
                expect(page.locator("#ask-scope")).to_contain_text("Research scope")
                expect(page.locator("#answer-result")).to_contain_text("Selected sources")
                page.locator('[data-view="papers"]').click()
                expect(page.locator("#papers-list .paper-card")).to_have_count(1)
                page.locator("#papers-list").get_by_role("button", name="Search this paper").click()
                page.locator("#search-query").fill("EGFR ATP")
                page.locator("#search-form").get_by_role(
                    "button", name="Search knowledge base"
                ).click()
                expect(page.locator("#search-results .result-card").first).to_be_visible()
                assert "研究 B" not in page.locator("#search-results").inner_text()
                report["checks"].append("切换语言保留选定范围；范围内检索不混入其他研究")
                page.locator('[data-view="papers"]').click()
                page.locator("#papers-scope").get_by_role(
                    "button", name="Clear paper selection"
                ).click()
                page.locator("#papers-fulltext").uncheck()
                expect(page.locator(".paper-card")).to_have_count(2)
                for checkbox in page.locator("[data-paper-id]").all():
                    checkbox.check()
                page.locator("#papers-ask").click()
                expect(page.locator("#ask-scope")).to_contain_text("2 papers")
                page.locator("#ask-query").fill("EGFR ATP")
                page.locator("#ask-form").get_by_role("button", name="Answer from sources").click()
                expect(page.locator("#answer-result .answer-text")).to_be_visible(timeout=15000)
                expect(
                    page.locator("#answer-result .chat-turn.assistant")
                    .last.locator(".notice")
                    .first
                ).to_contain_text("研究 B")
                report["checks"].append("多篇论文范围可选、清空，回答保存当次范围快照")
                page.locator('[data-view="papers"]').click()
                for width in (390, 820, 1440):
                    page.set_viewport_size({"width": width, "height": 900})
                    for locale in ("zh-CN", "en"):
                        page.locator("#language-select").select_option(locale)
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 1"
                        )
                        assert page.locator("main").evaluate(
                            "node => node.scrollWidth <= node.clientWidth + 1"
                        )
                page.screenshot(path=str(output / "papers_en.png"))
                page.locator("#language-select").select_option("zh-CN")
                page.screenshot(path=str(output / "papers_zh.png"))
                report["checks"].append("科研文献页中英文及 390/820/1440px 布局无溢出")
                browser.close()
        assert not report["page_errors"], report["page_errors"]
        assert not report["console_errors"], report["console_errors"]
        report["passed"] = True
        return report
    finally:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().output), ensure_ascii=False, indent=2))
