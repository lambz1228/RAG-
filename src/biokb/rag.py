"""检索增强问答：保存证据快照，校验引用编号，明确降级状态。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Callable

from .config import Settings
from .errors import ConflictError, KnowledgeError, ProviderError
from .providers import PROMPT_VERSION, LanguageModel
from .retrieval import RetrievalService
from .storage import Repository


class RAGService:
    def __init__(
        self,
        repository: Repository,
        retrieval: RetrievalService,
        model: LanguageModel,
        settings: Settings,
    ):
        self.repository, self.retrieval, self.model, self.settings = (
            repository,
            retrieval,
            model,
            settings,
        )

    @staticmethod
    def _extractive(evidence: list[dict]) -> str:
        return "以下为知识库中的相关原文摘录：\n\n" + "\n\n".join(
            f"{item['text'][:500]}{'…' if len(item['text']) > 500 else ''} [{index + 1}]"
            for index, item in enumerate(evidence)
        )

    @staticmethod
    def _validate_answer(result: dict, count: int) -> tuple[str, list[int], str]:
        if not isinstance(result, dict):
            raise ProviderError("模型回答必须为对象")
        answer = result.get("answer")
        citations = result.get("citations")
        status = result.get("status", "answered")
        if not isinstance(status, str) or status not in {"answered", "partial", "insufficient"}:
            raise ProviderError("模型回答状态不正确")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > 16000
            or not isinstance(citations, list)
        ):
            raise ProviderError("模型回答格式或长度不正确，已回退为原文证据")
        if status == "insufficient":
            if citations or re.search(r"\[\d+\]", answer):
                raise ProviderError("证据不足回答不应包含支持性引用")
            return answer.strip(), [], status
        if not citations or any(type(i) is not int or not 1 <= i <= count for i in citations):
            raise ProviderError("模型引用不存在的证据编号，已回退为原文证据")
        inline = {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
        if inline != set(citations):
            raise ProviderError("模型正文与引用列表不一致，已回退为原文证据")
        return answer.strip(), sorted(set(citations)), status

    @staticmethod
    def generate_answer(
        model,
        query: str,
        evidence: list[dict],
        trace: dict | None = None,
        history: list[dict] | None = None,
    ) -> tuple:
        """结构化结果至多纠正一次，所有尝试使用同一验证规则。"""
        trace = trace if trace is not None else {}
        trace.update(attempts=1, format_repair=False, validation_errors=[], response_sha256=[])
        # 历史非空才传入，兼容只接受固定参数的既有模型实现。
        if history:
            response = model.answer(query, evidence, history=history)
        else:
            response = model.answer(query, evidence)

        def remember(value):
            trace["response_sha256"].append(
                hashlib.sha256(
                    json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
            )

        remember(response)
        try:
            return RAGService._validate_answer(response, len(evidence))
        except ProviderError as exc:
            trace["validation_errors"].append(str(exc))
            repair = getattr(model, "repair_answer", None)
            if not callable(repair):
                raise
            trace.update(attempts=2, format_repair=True)
            if history:
                corrected = repair(query, evidence, response, str(exc), history=history)
            else:
                corrected = repair(query, evidence, response, str(exc))
            remember(corrected)
            try:
                return RAGService._validate_answer(corrected, len(evidence))
            except ProviderError as final_error:
                trace["validation_errors"].append(str(final_error))
                raise

    @staticmethod
    def _normalize_history(history: list[dict] | None) -> list[dict]:
        """会话记忆最多 3 轮，仅保留问答文本；格式错误立即失败，不静默丢弃。"""
        if history is None:
            return []
        if not isinstance(history, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("question"), str)
            or not isinstance(item.get("answer"), str)
            for item in history
        ):
            raise KnowledgeError("会话历史格式不正确")
        return [
            {"question": item["question"][:500], "answer": item["answer"][:600]}
            for item in history[-3:]
        ]

    def ask(
        self,
        query: str,
        strategy: str | None = None,
        top_k: int | None = None,
        progress: Callable | None = None,
        filters: dict | None = None,
        paper_ids: list[str] | None = None,
        context_neighbors: int = 0,
        history: list[dict] | None = None,
    ) -> dict:
        notify = progress or (lambda stage, percent: None)
        start = time.perf_counter()
        history = self._normalize_history(history)
        notify("检索知识库", 15)
        from .retrieval import validate_query

        count = self.settings.top_k if top_k is None else top_k
        if type(context_neighbors) is not int or not 0 <= context_neighbors <= 2:
            raise KnowledgeError("相邻证据扩展数量必须为 0–2 的整数")
        validate_query(query, count)
        search = self.retrieval.search(
            query,
            strategy,
            min(50, max(count, self.settings.retrieval_candidates)),
            filters=filters,
            paper_ids=paper_ids,
        )
        evidence = []
        warnings = list(search["warnings"])
        remaining = self.settings.max_context_chars
        documents: dict[str, int] = {}
        per_document_limit = (
            count
            if search["scope"]["kind"] == "papers" and len(search["scope"]["papers"]) == 1
            else self.settings.max_evidence_per_document
        )
        for hit in search["hits"]:
            if len(evidence) >= count:
                break
            if documents.get(hit["document_id"], 0) >= per_document_limit:
                continue
            expanded = self.repository.evidence_window(hit, context_neighbors)
            if len(expanded["text"]) <= remaining:
                hit = expanded
            if any(
                hit["document_id"] == old["document_id"]
                and max(
                    0,
                    min(hit["end_offset"], old["end_offset"])
                    - max(hit["start_offset"], old["start_offset"]),
                )
                / max(1, min(len(hit["text"]), len(old["text"])))
                >= 0.8
                for old in evidence
            ):
                continue
            if len(hit["text"]) > remaining:
                continue
            evidence.append(hit)
            documents[hit["document_id"]] = documents.get(hit["document_id"], 0) + 1
            remaining -= len(hit["text"])
        if self.repository.revision != search["index_revision"]:
            raise ConflictError("证据准备期间知识库发生变化，请重新提问")
        if len(evidence) != len(search["hits"]):
            warnings.append("已按证据数量、文献覆盖、重复程度和上下文预算筛选完整片段")
        used = list(range(1, len(evidence) + 1))
        generation = {"attempts": 0, "format_repair": False}
        if not evidence:
            mode, answer = (
                "insufficient_evidence",
                "当前知识库未找到足够的相关证据。请补充文献或调整问题。",
            )
        elif self.settings.llm_backend == "disabled":
            mode, answer = "extractive", self._extractive(evidence)
        else:
            notify("依据证据生成回答", 55)
            try:
                answer, used, status = self.generate_answer(
                    self.model, query, evidence, generation, history
                )
                if generation["format_repair"]:
                    warnings.append("模型首次输出未通过格式检查，已请求一次纠正并重新验证引用编号")
                mode = {
                    "answered": "generated",
                    "partial": "partial",
                    "insufficient": "insufficient_evidence",
                }[status]
            except ProviderError as exc:
                warnings.append(str(exc))
                mode, answer = "provider_fallback", self._extractive(evidence)
                used = list(range(1, len(evidence) + 1))
        result = {
            "id": "qa_" + uuid.uuid4().hex,
            "question": search["query"],
            "answer": answer,
            "mode": mode,
            "citations": [
                {"number": i + 1, "used": i + 1 in used, **row} for i, row in enumerate(evidence)
            ],
            "warnings": warnings,
            "retrieval": {key: value for key, value in search.items() if key != "hits"},
            "model": self.settings.llm_model
            if mode in {"generated", "partial"}
            or self.settings.llm_backend != "disabled"
            and mode == "insufficient_evidence"
            and evidence
            else "",
            "citation_validation": "编号与检索证据对应；事实蕴含关系仍需人工核对",
            "generation_fingerprint": getattr(self.model, "fingerprint", ""),
            "generation_validation": generation,
            "prompt_version": PROMPT_VERSION + ("-conversation" if history else ""),
            **({"conversation": {"history_pairs": len(history)}} if history else {}),
            "context": {
                "neighbors": context_neighbors,
                "source_count": len(
                    {hit.get("paper_id") or hit["document_id"] for hit in evidence}
                ),
                "max_chars": self.settings.max_context_chars,
                "used_chars": sum(len(hit["text"]) for hit in evidence),
                "retrieved_chunks": len(search["hits"]),
                "used_chunks": len(evidence),
            },
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
        notify("保存问答与证据", 90)
        self.repository.save_run(result["id"], "answer", search["query"], result)
        notify("完成", 100)
        return result
