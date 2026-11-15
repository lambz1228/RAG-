"""Flask 应用工厂：请求校验、业务装配与统一错误响应。"""

from __future__ import annotations

import hmac
import json
import uuid
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from .. import __version__
from ..answer_evaluation import summarize_bundle
from ..config import Settings
from ..container import Services, build_services
from ..errors import KnowledgeError
from ..literature import TOPICS, LiteratureService
from ..papers import build_catalog, list_papers, resolve_scope


def create_app(settings: Settings | None = None, services: Services | None = None) -> Flask:
    settings = settings or Settings.from_env()
    services = services or build_services(settings)
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_bytes + 64 * 1024
    # 静态资源不缓存，普通刷新即可拿到最新前端，避免 12 小时默认缓存造成改版不生效。
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.after_request
    def no_cache_html(response):
        # 页面本身禁止缓存；静态资源在上面已设为每次协商验证。
        if response.mimetype in {"text/html", "application/xhtml+xml"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    app.extensions["biokb"] = services
    literature = LiteratureService(services.ingestion, settings)
    app.extensions["literature"] = literature

    def body() -> dict:
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise KnowledgeError("请求正文必须是 JSON 对象")
        return value

    def number(value, default: int, maximum: int) -> int:
        if value is None:
            return default
        try:
            if isinstance(value, bool):
                raise ValueError
            parsed = int(value)
            if str(parsed) != str(value) or not 0 <= parsed <= maximum:
                raise ValueError
            return parsed
        except (TypeError, ValueError) as exc:
            raise KnowledgeError(f"数值参数必须为 0–{maximum} 的整数") from exc

    @app.before_request
    def protect_requests():
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(success=False, error="不允许跨来源请求", code="forbidden_origin"), 403
        if settings.host in {"127.0.0.1", "localhost", "::1"}:
            hostname = urlsplit("http://" + request.host).hostname
            if hostname not in {"127.0.0.1", "localhost", "::1"}:
                return jsonify(success=False, error="不允许的访问主机", code="invalid_host"), 403
        if (
            settings.api_token
            and request.path.startswith("/api/")
            and request.path != "/api/health"
        ):
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(
                supplied.encode("utf-8"), settings.api_token.encode("utf-8")
            ):
                return jsonify(
                    success=False, error="请输入有效的访问令牌", code="unauthorized"
                ), 401

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(KnowledgeError)
    def knowledge_error(error):
        return jsonify(success=False, error=str(error), code=error.code), error.status_code

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {
            400: "请求格式不正确",
            404: "接口或资源不存在",
            405: "请求方式不支持",
            413: "上传文件超过允许的体积",
        }
        return jsonify(
            success=False, error=messages.get(error.code, "HTTP 请求失败"), code="http_error"
        ), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error("请求执行异常，路径：%s，异常类型：%s", request.path, type(error).__name__)
        return jsonify(
            success=False, error="服务处理失败，请检查本地存储和配置", code="internal_error"
        ), 500

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__, literature_topics=TOPICS)

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/api/health")
    def health():
        return jsonify(
            status="ok", version=__version__, authentication_required=bool(settings.api_token)
        )

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify(
            success=True, stats=services.repository.stats(), configuration=settings.public()
        )

    @app.get("/api/config")
    def config():
        return jsonify(success=True, configuration=settings.public())

    @app.get("/api/documents")
    def documents():
        query = request.args.get("q", "")[:400]
        limit = number(request.args.get("limit"), 50, 200)
        offset = number(request.args.get("offset"), 0, 100000)
        return jsonify(
            success=True,
            **services.repository.list_documents(
                query,
                max(1, limit),
                offset,
                {
                    key: request.args[key]
                    for key in ("topic", "year", "content_type")
                    if request.args.get(key)
                },
            ),
        )

    @app.get("/api/facets")
    def facets():
        return jsonify(success=True, facets=services.repository.facets())

    @app.get("/api/literature/topics")
    def literature_topics():
        return jsonify(success=True, topics=TOPICS)

    @app.post("/api/literature/search")
    def literature_search():
        data = body()
        return jsonify(
            success=True,
            **literature.search(
                data.get("topic", "mitochondria"),
                data.get("keywords", ""),
                data.get("year_from", 2021),
                data.get("year_to", 2026),
                data.get("page", 1),
                data.get("page_size", 10),
                data.get("open_access", True),
            ),
        )

    @app.post("/api/literature/import")
    def literature_import():
        data = body()
        pmid, topic, fulltext = (
            data.get("pmid"),
            data.get("topic", "mitochondria"),
            data.get("include_fulltext", True),
        )
        import re

        if (
            not isinstance(pmid, str)
            or not re.fullmatch(r"[0-9]{1,10}", pmid)
            or topic not in TOPICS
            or type(fulltext) is not bool
        ):
            raise KnowledgeError("论文编号、主题或全文选项不正确")
        job = services.jobs.submit(
            "literature",
            lambda progress: literature.import_article(pmid, topic, fulltext, progress),
        )
        return jsonify(success=True, job=job), 202

    @app.get("/api/papers")
    def papers():
        fulltext = request.args.get("fulltext", "false")
        if fulltext not in {"true", "false"}:
            raise KnowledgeError("fulltext 必须为 true 或 false")
        return jsonify(
            success=True,
            **list_papers(
                services.repository,
                request.args.get("q", "")[:400],
                request.args.get("topic", "")[:200],
                request.args.get("year", "")[:4],
                fulltext == "true",
                max(1, number(request.args.get("limit"), 20, 100)),
                number(request.args.get("offset"), 0, 100000),
            ),
        )

    @app.get("/api/documents/<document_id>")
    def document(document_id):
        doc = services.repository.document(document_id)
        doc["paper"] = next(
            (
                paper
                for paper in build_catalog(services.repository.document_catalog())["items"]
                if document_id in paper["document_ids"]
            ),
            None,
        )
        return jsonify(success=True, document=doc)

    @app.delete("/api/documents/<document_id>")
    def delete_document(document_id):
        services.repository.delete_document(document_id)
        # 原始归档文件保留，数据库中的文献、分块和向量级联删除。
        return jsonify(success=True)

    @app.get("/api/documents/<document_id>/download")
    def download(document_id):
        doc = services.repository.document(document_id, False, False)
        target = settings.data_dir / "uploads" / (doc["content_hash"] + doc["file_type"])
        return send_file(
            target,
            as_attachment=True,
            download_name=doc["filename"],
            mimetype="application/octet-stream",
        )

    @app.post("/api/documents/upload")
    def upload():
        file = request.files.get("file")
        if file is None or not file.filename:
            raise KnowledgeError("请选择要上传的文献")
        metadata = {
            key: request.form[key]
            for key in ("title", "url", "source", "topic", "year", "content_type", "doi")
            if request.form.get(key)
        }
        content = file.read(settings.max_upload_bytes + 1)
        filename = file.filename
        job = services.jobs.submit(
            "ingest",
            lambda progress: services.ingestion.ingest(filename, content, metadata, progress),
        )
        return jsonify(success=True, job=job), 202

    @app.post("/api/index/rebuild")
    def rebuild():
        body()
        job = services.jobs.submit("reindex", services.ingestion.reindex, {"kind": "reindex"})
        return jsonify(success=True, job=job), 202

    @app.post("/api/search")
    def search():
        data = body()
        return jsonify(
            success=True,
            **services.retrieval.search(
                data.get("query", ""),
                data.get("strategy"),
                data.get("top_k"),
                data.get("document_id", ""),
                data.get("filters"),
                data.get("paper_ids"),
            ),
        )

    @app.post("/api/ask")
    def ask():
        return submit_answer(body())

    def submit_answer(data):
        from ..retrieval import validate_query

        validate_query(data.get("query", ""), data.get("top_k", settings.top_k))
        from ..quality import validate_filters

        filters = validate_filters(data.get("filters"))
        resolve_scope(services.repository, data.get("paper_ids"))
        neighbors = number(data.get("context_neighbors"), 0, 2)
        conversation_id = str(data.get("conversation_id") or "")
        history: list[dict] = []
        if conversation_id:
            # 会话不存在立即返回 404；turn_id 在重试间复用，消息写入幂等。
            services.repository.get_conversation(conversation_id)
            data.setdefault("turn_id", uuid.uuid4().hex)
            history = services.repository.conversation_history(conversation_id)

        def run(progress):
            if conversation_id:
                services.repository.append_message(
                    conversation_id,
                    "user",
                    str(data["query"]),
                    message_id=f"{data['turn_id']}_u",
                )
            result = services.rag.ask(
                data["query"],
                data.get("strategy"),
                data.get("top_k"),
                progress,
                filters,
                data.get("paper_ids"),
                neighbors,
                history,
            )
            if conversation_id:
                services.repository.append_message(
                    conversation_id,
                    "assistant",
                    result["answer"],
                    run_id=result["id"],
                    message_id=f"{data['turn_id']}_a",
                )
            return result

        job = services.jobs.submit(
            "answer",
            run,
            {
                "kind": "answer",
                "data": {
                    key: data[key]
                    for key in (
                        "query",
                        "strategy",
                        "top_k",
                        "filters",
                        "paper_ids",
                        "context_neighbors",
                        "conversation_id",
                        "turn_id",
                    )
                    if key in data
                },
            },
        )
        return jsonify(success=True, job=job), 202

    @app.get("/api/jobs/<job_id>")
    def job(job_id):
        return jsonify(success=True, job=services.jobs.get(job_id))

    @app.get("/api/jobs")
    def jobs():
        return jsonify(success=True, items=services.jobs.recent())

    @app.post("/api/jobs/<job_id>/retry")
    def retry_job(job_id):
        body()
        previous = services.jobs.get(job_id)
        if previous["status"] not in {"failed", "interrupted"}:
            raise KnowledgeError("仅失败或中断的任务可以重试")
        retry = previous.get("retry") or {}
        if retry.get("kind") == "answer":
            return submit_answer(retry["data"])
        if retry.get("kind") == "evaluation":
            return submit_evaluation(retry["data"])
        if retry.get("kind") == "reindex":
            return jsonify(
                success=True, job=services.jobs.submit("reindex", services.ingestion.reindex, retry)
            ), 202
        raise KnowledgeError("上传任务需要重新选择原文件后提交")

    @app.get("/api/history")
    def history():
        return jsonify(success=True, items=services.repository.history())

    @app.get("/api/history/<run_id>")
    def history_item(run_id):
        return jsonify(success=True, result=services.repository.run(run_id))

    @app.get("/api/history/<run_id>/export")
    def export_history(run_id):
        result = services.repository.run(run_id)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        response = app.response_class(payload, mimetype="application/json")
        response.headers["Content-Disposition"] = f'attachment; filename="{result["id"]}.json"'
        return response

    @app.post("/api/conversations")
    def create_conversation():
        data = body()
        conversation = services.repository.create_conversation(str(data.get("title") or ""))
        return jsonify(success=True, conversation=conversation), 201

    @app.get("/api/conversations")
    def conversations():
        return jsonify(success=True, items=services.repository.list_conversations())

    @app.get("/api/conversations/<conversation_id>")
    def conversation(conversation_id):
        return jsonify(
            success=True,
            conversation=services.repository.get_conversation(conversation_id),
            messages=services.repository.conversation_messages(conversation_id),
        )

    @app.delete("/api/conversations/<conversation_id>")
    def remove_conversation(conversation_id):
        services.repository.delete_conversation(conversation_id)
        return jsonify(success=True)

    @app.post("/api/answer-evaluations")
    def answer_quality():
        bundle_file, reviews_file = request.files.get("bundle"), request.files.get("reviews")
        if bundle_file is None or reviews_file is None:
            raise KnowledgeError("请选择协调者评分运行包和人工评分CSV")
        try:
            bundle = json.loads(bundle_file.read())
            report = summarize_bundle(bundle, reviews_file.read())
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise KnowledgeError("评分材料格式不正确或与本次答卷不一致") from exc
        report["id"] = "review_" + uuid.uuid4().hex
        services.repository.save_run(report["id"], "answer_evaluation", "回答质量评价", report)
        return jsonify(success=True, report=report)

    @app.get("/api/evaluations")
    def evaluations():
        return jsonify(success=True, items=services.repository.history(kind="evaluation"))

    @app.post("/api/evaluations")
    def start_evaluation():
        return submit_evaluation(body())

    def submit_evaluation(data):
        from ..evaluation import evaluate

        dataset = data.get("dataset")
        if (
            not isinstance(dataset, dict)
            or not isinstance(dataset.get("items"), list)
            or not 1 <= len(dataset["items"]) <= 200
        ):
            raise KnowledgeError("网页评估集需包含 1–200 道题，较大实验请使用命令行")
        strategies = data.get("strategies", ["bm25", "dense", "hybrid"])
        if (
            not isinstance(strategies, list)
            or not strategies
            or len(strategies) > 4
            or any(
                not isinstance(s, str) or s not in {"bm25", "dense", "hybrid", "hybrid_rerank"}
                for s in strategies
            )
        ):
            raise KnowledgeError("评估检索方式不正确")
        k = number(data.get("k"), 10, 50)

        def run(progress):
            progress("运行检索对比实验", 15)
            report = evaluate(services.retrieval, dataset, strategies, k)
            report["id"] = "eval_" + uuid.uuid4().hex
            services.repository.save_run(
                report["id"], "evaluation", str(data.get("name") or "检索对比实验")[:200], report
            )
            progress("评估完成", 100)
            return report

        retry = {
            "kind": "evaluation",
            "data": {
                "dataset": dataset,
                "strategies": strategies,
                "k": k,
                "name": str(data.get("name") or "检索对比实验")[:200],
            },
        }
        return jsonify(success=True, job=services.jobs.submit("evaluation", run, retry)), 202

    return app
