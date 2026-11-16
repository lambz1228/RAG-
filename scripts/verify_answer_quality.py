"""评分材料上传、待复核状态、导出和双语布局；使用明确的受控测试答卷。"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from verify_browser import Settings, running_app
from verify_workspace import check_translations, navigate

from biokb.experiments import export_review, write_json


def verify(output):
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=False)
    report = {
        "passed": False,
        "scope": "受控测试夹具，不是人工评分或真实模型质量实验",
        "page_errors": [],
    }
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            run.mkdir()
            write_json(
                run / "protocol.json",
                {
                    "scope": "界面测试夹具",
                    "question_ids": ["fixture-q1"],
                    "dataset": {"sha256": "fixture"},
                    "arms": [{"name": "basic_rag", "strategy": "dense"}],
                },
            )
            questions = [
                {"qid": "fixture-q1", "question": "测试问题", "reference": {"answerable": True}}
            ]
            results = [
                {
                    "qid": "fixture-q1",
                    "arm": "basic_rag",
                    "answer": "受控界面测试答案，不用于学术评价。",
                    "mode": "generated",
                    "citations": [],
                }
            ]
            (run / "results.jsonl").write_bytes((json.dumps(results[0]) + "\n").encode())
            export_review(run, questions, results, 1)
            with (
                running_app(Settings(data_dir=root / "data")) as (url, services),
                sync_playwright() as engine,
            ):
                browser = engine.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                page.goto(url + "/#evaluation")
                expect(page.locator("#optional-answer-review")).not_to_have_attribute("open", "")
                page.get_by_text("人工评分（可选）", exact=True).click()
                expect(page.locator("#answer-quality-form")).to_contain_text(
                    "不作为运行或验收前置条件"
                )
                page.locator("#answer-quality-bundle").set_input_files(
                    str(run / "评分运行包_协调者保管.json")
                )
                page.locator("#answer-quality-reviews").set_input_files(str(run / "盲评表.csv"))
                page.locator("#answer-quality-form").get_by_role(
                    "button", name="导入评分并汇总"
                ).click()
                expect(page.locator("#answer-quality-status")).to_contain_text("整体指标保持空值")
                expect(page.locator("#answer-quality-result")).to_contain_text("待评分或不适用")
                with page.expect_download() as downloaded:
                    page.locator("#answer-quality-result").get_by_role(
                        "button", name="导出完整结果"
                    ).click()
                target = output / "export.json"
                downloaded.value.save_as(target)
                assert json.loads(target.read_text())["reviewed_answers"] == 0
                page.locator("#language-select").select_option("en")
                expect(page.locator("#answer-quality-result")).to_contain_text(
                    "Pending or not applicable"
                )
                check_translations(page)
                page.locator("#answer-quality-result").scroll_into_view_if_needed()
                page.screenshot(path=str(output / "answer-quality.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                navigate(page, "evaluation")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                browser.close()
            assert not report["page_errors"]
            report["passed"] = True
    finally:
        write_json(output / "report.json", report)
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().output), ensure_ascii=False, indent=2))
