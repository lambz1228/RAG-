"""用明确的测试文字联调真实模型；报告不输出凭据和原始服务响应。"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biokb.config import Settings
from biokb.errors import KnowledgeError
from biokb.providers import EmbeddingProvider, LanguageModel
from biokb.rag import RAGService
from biokb.reranking import Reranker


def check(settings: Settings) -> dict:
    report = {
        "configuration": settings.public(),
        "checks": {},
        "scope": "明确标注的接口测试文字，不构成模型质量实验",
    }
    evidence = [
        {
            "chunk_id": "test",
            "title": "接口测试资料（非科研结论）",
            "text": "这是一条接口测试记录：样本 A 的编号是 17。",
        }
    ]
    for kind in ["embedding", "generation", "reranking"]:
        started = time.perf_counter()
        try:
            if kind == "embedding":
                if settings.embedding_backend == "hash":
                    report["checks"][kind] = {"status": "not_configured"}
                    continue
                vectors = EmbeddingProvider(settings).embed_batch(
                    ["接口测试：样本 A", "Interface test: sample A"]
                )
                result = {"dimensions": len(vectors[0]), "count": len(vectors)}
            elif kind == "generation":
                if settings.llm_backend == "disabled":
                    report["checks"][kind] = {"status": "not_configured"}
                    continue
                trace = {}
                answer, citations, status = RAGService.generate_answer(
                    LanguageModel(settings), "测试记录中样本 A 的编号是多少？", evidence, trace
                )
                result = {
                    "answer": answer,
                    "citations": citations,
                    "answer_status": status,
                    "generation_validation": trace,
                }
            else:
                if settings.reranker_backend == "disabled":
                    report["checks"][kind] = {"status": "not_configured"}
                    continue
                result = {"results": len(Reranker(settings).rank("样本 A", evidence))}
            report["checks"][kind] = {
                "status": "passed",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                **result,
            }
        except KnowledgeError as exc:
            report["checks"][kind] = {"status": "failed", "error": str(exc)}
    report["real_embedding_and_generation_passed"] = all(
        report["checks"][key]["status"] == "passed" for key in ["embedding", "generation"]
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check(Settings.from_env())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["real_embedding_and_generation_passed"] else 1)
