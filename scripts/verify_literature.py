"""在隔离库验证采集页面、实际解析入库与双语移动布局；官方 HTTP 使用固定夹具。"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from verify_browser import ROOT, Settings, running_app
from verify_workspace import check_translations, navigate


def verify(output):
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=False)
    sample = json.loads((ROOT / "examples/open_demo/PMC12142011.json").read_text())
    response = {
        "hitCount": 1,
        "resultList": {
            "result": [
                {
                    "id": sample["pmid"],
                    "pmid": sample["pmid"],
                    "pmcid": sample["pmcid"],
                    "title": sample["title"],
                    "pubYear": "2025",
                    "abstractText": "Peroxisomal biogenesis factors. " * 12,
                    "isOpenAccess": "Y",
                }
            ]
        },
    }
    errors = []
    from unittest.mock import patch

    from biokb.literature import TOPICS, LiteratureService

    def official_fixture(self, path, params=None, **kwargs):
        return (
            json.dumps(response).encode()
            if path == "/search"
            else (ROOT / "examples/open_demo/PMC12142011.xml").read_bytes()
        )

    report = {"scope": "固定官方响应的浏览器功能测试；不代表在线检索与模型质量", "passed": False}
    try:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(LiteratureService, "_get", official_fixture),
            running_app(Settings(data_dir=Path(temporary))) as (url, services),
            sync_playwright() as engine,
        ):
            browser = engine.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/#sources")
            expect(page.locator("#literature-topic option")).to_have_count(len(TOPICS))
            page.locator("#literature-topic").select_option("dna_maintenance")
            expect(page.locator("#literature-topic")).to_have_value("dna_maintenance")
            page.locator("#literature-topic").select_option("peroxisome")
            page.locator("#literature-form").get_by_role("button", name="检索生物文献").click()
            expect(page.locator(".literature-card")).to_have_count(1)
            page.get_by_text("查看摘要", exact=True).click()
            expect(page.locator(".abstract-disclosure p")).to_be_visible()
            page.locator(".literature-card").get_by_role("button", name="导入知识库").click()
            expect(page.locator("#literature-status")).to_contain_text(
                "已导入开放全文", timeout=15000
            )
            assert services.repository.stats()["documents"] == 1
            page.screenshot(path=str(output / "literature_zh.png"))
            page.locator("#language-select").select_option("en")
            check_translations(page)
            expect(page.locator('#literature-topic option[value="dna_maintenance"]')).to_have_text(
                "DNA replication and repair"
            )
            assert (
                page.evaluate("""async () => {
                const {topicName} = await import('/static/papers.js');
                const {translate} = await import('/static/i18n.js');
                return translate(topicName('dna_maintenance'));
            }""")
                == "DNA replication and repair"
            )
            expect(page.locator("#literature-status")).to_contain_text("Open full text imported")
            navigate(page, "ask")
            expect(page.locator(".chat-welcome")).to_be_visible()
            page.screenshot(path=str(output / "ask_en.png"))
            for width in (390, 820, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                for view in ("sources", "ask", "overview"):
                    navigate(page, view)
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    ), (width, view)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator("#language-select").select_option("zh-CN")
            navigate(page, "overview")
            page.screenshot(path=str(output / "overview_zh.png"))
            browser.close()
            assert not errors, errors
            report.update(
                passed=True,
                page_errors=errors,
                checks=[
                    "18个主题同步展示及新主题双语切换",
                    "官方检索响应展示",
                    "许可和标识校验后实际入库",
                    "来源保存",
                    "双语状态与新导航",
                    "390/820/1440px 无整页溢出",
                ],
            )
    finally:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().output), ensure_ascii=False, indent=2))
