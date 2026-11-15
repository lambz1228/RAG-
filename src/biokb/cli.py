"""统一命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .container import build_services
from .errors import KnowledgeError
from .evaluation import evaluate, write_report


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="基于 RAG 技术的生物领域知识库系统")
    root.add_argument("--data-dir", type=Path, help="运行数据目录；默认 data/biokb")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动 Web 工作台")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--dev", action="store_true", help="启用开发服务器，不启用远程调试器")
    import_command = commands.add_parser("import", help="导入文献或语料目录")
    import_command.add_argument("path", type=Path)
    for name in ("search", "ask"):
        cmd = commands.add_parser(name, help="检索文献" if name == "search" else "证据问答")
        cmd.add_argument("query")
        cmd.add_argument("--strategy", choices=["bm25", "dense", "hybrid", "hybrid_rerank"])
        cmd.add_argument("--top-k", type=int)
    commands.add_parser("stats", help="查看文献、分块、向量与问答数量")
    commands.add_parser("doctor", help="检查配置和索引一致性，不调用模型")
    commands.add_parser("reindex", help="使用当前向量模型重建索引")
    revision = commands.add_parser("create-revision", help="只读原库并创建独立资料质量版本")
    revision.add_argument("source", type=Path)
    revision.add_argument("target", type=Path)
    experiment = commands.add_parser("evaluate", help="执行文档检索对比实验")
    experiment.add_argument("dataset", type=Path)
    experiment.add_argument(
        "--strategies",
        nargs="+",
        choices=["bm25", "dense", "hybrid", "hybrid_rerank"],
        default=["bm25", "dense", "hybrid"],
    )
    experiment.add_argument("--k", type=int, default=10)
    experiment.add_argument("--output", type=Path, required=True)
    experiment.add_argument(
        "--source-map", type=Path, help="经核验的旧编号到库内 source_id 映射 JSON"
    )
    return root


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    services = None
    try:
        if args.command == "create-revision":
            from .revisions import create_revision

            result = create_revision(args.source, args.target)
            print(
                json.dumps(
                    {key: value for key, value in result.items() if key != "entries"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        settings = Settings.from_env()
        if args.data_dir:
            settings = replace(settings, data_dir=args.data_dir.resolve())
        if args.command == "serve":
            settings = replace(
                settings,
                host=args.host if args.host is not None else settings.host,
                port=args.port if args.port is not None else settings.port,
            ).validate()
            from .web.app import create_app

            app = create_app(settings)
            services = app.extensions["biokb"]
            print(f"生物领域知识库：http://{settings.host}:{settings.port}", flush=True)
            if args.dev:
                app.run(host=settings.host, port=settings.port, debug=False, use_reloader=False)
            else:
                from waitress import serve

                serve(app, host=settings.host, port=settings.port, threads=4)
            return 0
        services = build_services(settings)
        if args.command == "import":
            if args.path.is_dir():
                result = services.ingestion.import_directory(args.path)
            elif args.path.is_file():
                if args.path.stat().st_size > settings.max_upload_bytes:
                    raise KnowledgeError("文件超过 25 MB")
                result = services.ingestion.ingest(args.path.name, args.path.read_bytes())
                result["document"].pop("text", None)
            else:
                raise KnowledgeError("导入路径不存在")
        elif args.command == "search":
            result = services.retrieval.search(args.query, args.strategy, args.top_k)
        elif args.command == "ask":
            result = services.rag.ask(args.query, args.strategy, args.top_k)
        elif args.command == "reindex":
            result = services.ingestion.reindex()
        elif args.command == "evaluate":
            try:
                dataset = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise KnowledgeError("无法读取评估集 JSON") from exc
            source_map = None
            if args.source_map:
                try:
                    source_map = json.loads(args.source_map.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError) as exc:
                    raise KnowledgeError("无法读取出处映射 JSON") from exc
            result = evaluate(services.retrieval, dataset, args.strategies, args.k, source_map)
            write_report(result, args.output)
            result = {
                "output": str(args.output.resolve()),
                "results": {
                    name: {k: v for k, v in values.items() if k != "details"}
                    for name, values in result["results"].items()
                },
            }
        else:
            result = services.repository.stats()
            if args.command == "doctor":
                fingerprints = result["index_fingerprints"]
                result.update(
                    configuration=settings.public(),
                    index_compatible=not fingerprints
                    or fingerprints == [services.retrieval.embedder.fingerprint],
                    database=str(settings.database_path),
                    model_connectivity="未调用模型，连接能力需单独验证",
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("failed") or result.get("index_compatible") is False else 0
    except KnowledgeError as exc:
        print(
            json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False), file=sys.stderr
        )
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if services:
            services.jobs.close()


if __name__ == "__main__":
    raise SystemExit(main())
