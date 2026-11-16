"""在临时知识库和独立无头浏览器中验收完整流程，不写入正式语料库。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.web.app import create_app  # noqa: E402


@contextmanager
def running_app(settings):
    from werkzeug.serving import WSGIRequestHandler, make_server

    class QuietHandler(WSGIRequestHandler):
        def log(self, *args, **kwargs):
            pass

    application = create_app(settings)
    server = make_server("127.0.0.1", 0, application, threaded=True, request_handler=QuietHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", application.extensions["biokb"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        application.extensions["biokb"].jobs.close()


def verify(output: Path, snapshot_data: Path | None = None) -> dict:
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": [], "page_errors": [], "console_errors": []}
    fixture = {
        "title": "线粒体能量代谢测试文献",
        "source_id": "browser-fixture",
        "content": ("这是自动化测试材料。线粒体参与能量代谢，mitochondria 与 ATP 作为检索词。" * 14)
        + "<script>window.fixtureInjected = true</script>",
    }
    raw = json.dumps(fixture, ensure_ascii=False).encode("utf-8")
    try:
        with (
            tempfile.TemporaryDirectory(prefix="biokb-browser-") as temporary,
            sync_playwright() as engine,
        ):
            browser = engine.chromium.launch(headless=True)
            report["browser"] = browser.version
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            # 认证面板出现时的 401 是预期响应，其他错误单独收集。
            page.on(
                "console",
                lambda message: (
                    report["console_errors"].append(message.text)
                    if message.type == "error" and "401" not in message.text
                    else None
                ),
            )
            settings = Settings(
                data_dir=Path(temporary),
                chunk_size=200,
                chunk_overlap=30,
                api_token="browser-test-only",
            )
            with running_app(settings) as (url, services):
                page.goto(url)
                expect(page.locator("#access-panel")).to_be_visible()
                page.locator("#access-token").fill(settings.api_token)
                page.get_by_role("button", name="连接知识库").click()
                expect(page.locator("#access-panel")).to_be_hidden()
                expect(page.locator("#stats .stat-number").first).to_have_text("0")
                report["checks"].append("访问令牌与空知识库")

                page.locator('[data-view="documents"]').click()
                file = {"name": "测试文献.json", "mimeType": "application/json", "buffer": raw}
                page.locator("#document-file").set_input_files(file)
                page.get_by_role("button", name="上传并建立索引").click()
                expect(page.locator("#upload-status")).to_contain_text("入库完成", timeout=20000)
                expect(page.locator("#document-total")).to_have_text("1 篇")
                page.locator("#documents-table").get_by_role(
                    "button", name=fixture["title"]
                ).click()
                expect(page.locator("#document-dialog")).to_be_visible()
                expect(page.locator("#document-dialog-body")).to_contain_text("片段 1")
                assert page.evaluate("window.fixtureInjected") is None
                with page.expect_download() as download_info:
                    page.get_by_role("button", name="下载原始文件").click()
                original = output / "downloaded_fixture.json"
                download_info.value.save_as(original)
                assert original.read_bytes() == raw
                page.get_by_role("button", name="关闭文献详情").click()
                report["checks"].append("中文上传、分块详情、原始字节下载与文本安全显示")

                page.locator("#document-file").set_input_files(file)
                page.get_by_role("button", name="上传并建立索引").click()
                expect(page.locator("#upload-status")).to_contain_text(
                    "没有重复添加", timeout=20000
                )
                page.locator("#document-file").set_input_files(
                    {"name": "损坏.pdf", "mimeType": "application/pdf", "buffer": b"not a pdf"}
                )
                page.get_by_role("button", name="上传并建立索引").click()
                expect(page.locator("#upload-status")).to_contain_text("操作失败", timeout=20000)
                expect(page.get_by_role("button", name="上传并建立索引")).to_be_enabled()
                assert services.repository.stats()["documents"] == 1
                report["checks"].append("重复文献与损坏 PDF 的可见错误")

                page.locator('[data-view="search"]').click()
                page.locator("#search-query").fill("线粒体 ATP")
                for strategy in ("bm25", "dense", "hybrid"):
                    page.locator("#search-strategy").select_option(strategy)
                    page.get_by_role("button", name="检索知识库", exact=True).click()
                    expect(page.locator("#search-status")).to_contain_text("找到")
                    expect(page.locator("#search-results .result-card").first).to_contain_text(
                        fixture["title"]
                    )
                report["checks"].append("三种检索策略与来源片段")

                page.locator('[data-view="ask"]').click()
                question = "线粒体与 ATP 的关系是什么？"
                page.locator("#ask-query").fill(question)
                page.get_by_role("button", name="依据文献回答").click()
                expect(page.locator("#answer-result")).to_contain_text(
                    "原文证据摘录", timeout=20000
                )
                expect(page.locator("#answer-result")).to_contain_text("[1]")
                page.locator("#answer-result .citation-link").first.click()
                expect(page.locator('#answer-result [data-evidence-number="1"]')).to_be_focused()
                page.locator('#answer-result [data-evidence-number="1"]').get_by_role(
                    "button", name="查看文献与文本块"
                ).click()
                expect(page.locator("#document-dialog .selected-evidence")).to_be_visible()
                page.get_by_role("button", name="关闭文献详情").click()
                report["checks"].append("搜索和回答并存时引用正确聚焦，以及原文分块高亮定位")
                with page.expect_download() as download_info:
                    page.get_by_role("button", name="导出回答与证据").click()
                exported_path = output / "answer_export.json"
                download_info.value.save_as(exported_path)
                exported = json.loads(exported_path.read_text(encoding="utf-8"))
                assert exported["mode"] == "extractive" and exported["citations"]
                expect(page.locator("#toast")).to_be_hidden(timeout=7000)
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(output / "answer.png"), full_page=True)
                page.locator('[data-view="history"]').click()
                page.reload()
                page.locator("#history-list").get_by_role(
                    "button", name=question, exact=True
                ).click()
                expect(page.locator("#answer-result")).to_contain_text("原文证据摘录")
                report["checks"].append("证据问答、引用编号、导出与刷新后的历史回看")

                page.locator('[data-view="evaluation"]').click()
                dataset = {
                    "origin": "浏览器流程测试夹具",
                    "items": [
                        {"qid": "one", "question": "线粒体 ATP", "gold_doc_id": "browser-fixture"}
                    ],
                }
                page.locator("#evaluation-file").set_input_files(
                    {
                        "name": "测试评估.json",
                        "mimeType": "application/json",
                        "buffer": json.dumps(dataset).encode(),
                    }
                )

                class FixtureReranker:
                    fingerprint = "browser-functional-fixture"

                    def rank(self, query, hits):
                        return [{**hit, "rerank_score": 1.0} for hit in hits]

                services.retrieval.reranker = FixtureReranker()
                page.locator("#evaluation-rerank").check()
                page.get_by_role("button", name="运行检索对比").click()
                expect(page.locator("#evaluation-result")).to_contain_text(
                    "检索对比结果", timeout=20000
                )
                with page.expect_download() as download_info:
                    page.get_by_role("button", name="导出完整结果").click()
                metrics_path = output / "evaluation_export.json"
                download_info.value.save_as(metrics_path)
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                assert set(metrics["results"]) == {"bm25", "dense", "hybrid", "hybrid_rerank"}
                assert all(result["Hit@10"] == 1 for result in metrics["results"].values())
                page.get_by_text("逐题结果与失败分析", exact=True).click()
                expect(page.locator(".evaluation-details")).to_contain_text("全部覆盖")
                report["checks"].append("四路评估界面、逐题结果与导出（重排序使用功能夹具）")

                page.locator('[data-view="settings"]').click()
                page.get_by_role("button", name="重建向量索引").click()
                expect(page.locator("#rebuild-status")).to_contain_text("索引完成", timeout=20000)
                report["checks"].append("索引重建及进度")

                page.locator(".workspace-brand").click()
                expect(page.locator('[data-panel="overview"]')).to_be_visible()
                page.screenshot(path=str(output / "overview.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                for view in (
                    "overview",
                    "documents",
                    "search",
                    "ask",
                    "history",
                    "evaluation",
                    "settings",
                ):
                    page.locator("#toggle-sidebar").click()
                    page.locator(f'[data-view="{view}"]').click()
                    expect(page.locator(f'[data-panel="{view}"]')).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    ), f"移动端溢出：{view}"
                page.locator("#toggle-sidebar").click()
                page.locator('[data-view="overview"]').click()
                page.screenshot(path=str(output / "mobile.png"), full_page=True)
                report["checks"].append("品牌导航及 390px 移动端七页面无横向溢出")

                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator('[data-view="documents"]').click()
                page.once("dialog", lambda dialog: dialog.accept())
                page.get_by_role("button", name="移出知识库").click()
                expect(page.locator("#document-total")).to_have_text("0 篇")
                assert services.repository.stats()["chunks"] == 0
                assert services.repository.run(exported["id"])["citations"] == exported["citations"]
                report["checks"].append("移出文献、索引级联删除与证据快照保留")

                services.ingestion.ingest(
                    "filter.txt",
                    "线粒体 ATP 是用于验证界面筛选的测试内容。".encode(),
                    {"topic": "测试主题", "year": "2025", "content_type": "abstract"},
                )
                services.ingestion.ingest(
                    "stub.txt",
                    "细胞生物学第4章《内膜系统》的细分内容。".encode(),
                    {"year": "2024", "title": "待补充测试条目"},
                )
                page.reload()
                page.locator("#documents-content_type").select_option("reference_stub")
                expect(page.locator("#document-total")).to_have_text("1 篇")
                page.get_by_role("button", name="待补充测试条目", exact=True).click()
                expect(page.locator("#document-dialog-body")).to_contain_text("未用于检索回答")
                page.get_by_role("button", name="关闭文献详情").click()
                page.locator('[data-view="search"]').click()
                page.locator("#search-content_type").select_option("reference_stub")
                page.locator("#search-query").fill("内膜系统")
                page.get_by_role("button", name="检索知识库", exact=True).click()
                expect(page.locator("#search-status")).to_contain_text("找到 0 个片段")
                page.locator('[data-view="ask"]').click()
                page.locator("#ask-form .ask-advanced > summary").click()
                page.locator("#ask-year").select_option("2025")
                page.locator("#ask-topic").select_option("测试主题")
                page.locator("#ask-query").fill("线粒体 ATP")
                page.get_by_role("button", name="依据文献回答").click()
                expect(page.locator("#answer-result")).to_contain_text("界面筛选的测试内容")
                assert len(services.repository.eligible_source_ids()) == 1
                report["checks"].append("资料类型、主题和年份筛选，以及待补充条目不参与回答")
                services.repository.save_job(
                    {
                        "id": "restart-browser-fixture",
                        "kind": "answer",
                        "status": "running",
                        "stage": "测试中断",
                        "progress": 30,
                        "created_at": 9999999999,
                        "retry": {
                            "kind": "answer",
                            "data": {"query": "线粒体 ATP", "strategy": "bm25"},
                        },
                    }
                )

                if os.name == "nt":
                    environment = dict(os.environ, BIOKB_API_TOKEN=settings.api_token)
                    checked = subprocess.run(
                        [
                            "powershell",
                            "-NoProfile",
                            "-File",
                            str(ROOT / "scripts/health_check.ps1"),
                            "-BaseUrl",
                            url,
                        ],
                        env=environment,
                        capture_output=True,
                        timeout=30,
                    )
                    if checked.returncode and b"UnauthorizedAccess" in checked.stderr:
                        # 保留系统执行策略，仅以HTTP逐项核验等价的接口行为。
                        import requests

                        report.setdefault("platform_limitations", []).append(
                            "Windows执行策略阻止未签名PowerShell脚本；未执行该脚本，改用等价HTTP检查"
                        )
                        headers = {"Authorization": "Bearer " + settings.api_token}
                        for route in (
                            "/",
                            "/api/health",
                            "/api/dashboard",
                            "/api/config",
                            "/api/documents?limit=1",
                        ):
                            response = requests.get(url + route, headers=headers, timeout=10)
                            assert response.status_code == 200, route
                            if route == "/api/health":
                                assert response.json()["status"] == "ok"
                        report["checks"].append("Python等价健康检查：五个HTTP接口")
                    else:
                        assert checked.returncode == 0, "PowerShell 健康检查未通过"
                        report["checks"].append("PowerShell 五个健康接口")

            with running_app(settings) as (url, services):
                page.goto(url + "#history")
                page.locator("#access-token").fill(settings.api_token)
                page.get_by_role("button", name="连接知识库").click()
                expect(page.locator("#jobs-list")).to_contain_text("服务已重启")
                page.locator("#jobs-list .job-row").filter(has_text="服务已重启").get_by_role(
                    "button", name="重新提交"
                ).click()
                expect(page.locator('[data-panel="ask"]')).to_be_visible(timeout=20000)
                expect(page.locator("#answer-result")).to_contain_text("界面筛选的测试内容")
                page.screenshot(path=str(output / "recovered_answer.png"), full_page=True)
                report["checks"].append("服务重启标记中断、持久任务可见、重新提交并展示结果")

            if snapshot_data:
                with running_app(Settings(data_dir=snapshot_data.resolve())) as (url, services):
                    page.goto(url)
                    expect(page.locator("#stats .stat-number").first).to_have_text(
                        str(services.repository.stats()["documents"])
                    )
                    page.screenshot(path=str(output / "knowledge_overview.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=str(output / "knowledge_mobile.png"), full_page=True)
                    report["snapshot_stats"] = services.repository.stats()
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
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/refactor/browser")
    parser.add_argument("--snapshot-data", type=Path)
    args = parser.parse_args()
    result = verify(args.output, args.snapshot_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
