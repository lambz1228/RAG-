"""独立的重排序适配器；索引与生成模块不依赖具体服务。"""

import hashlib
import json
import math

from .config import Settings
from .errors import ProviderError
from .providers import post_json


class Reranker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "backend": settings.reranker_backend,
                    "model": settings.reranker_model,
                    "endpoint": settings.reranker_base_url,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:24]

    def rank(self, query: str, hits: list[dict]) -> list[dict]:
        if self.settings.reranker_backend == "disabled":
            raise ProviderError("未配置重排序模型，请选择混合检索或配置重排序接口")
        if not hits:
            return []
        data = post_json(
            self.settings.reranker_base_url.rstrip("/") + "/rerank",
            {
                "model": self.settings.reranker_model,
                "query": query,
                "documents": [hit["title"] + "\n" + hit["text"] for hit in hits],
                "top_n": len(hits),
                "return_documents": False,
            },
            self.settings.reranker_api_key,
            self.settings.request_timeout,
        )
        rows = data.get("results")
        if not isinstance(rows, list) or len(rows) != len(hits):
            raise ProviderError("重排序返回的候选数量不完整")
        seen = set()
        output = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError("重排序结果格式错误")
            index, score = row.get("index"), row.get("relevance_score")
            if type(index) is not int or not 0 <= index < len(hits) or index in seen:
                raise ProviderError("重排序候选编号缺失、重复或越界")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ProviderError("重排序分数无效")
            seen.add(index)
            output.append({**hits[index], "rerank_score": float(score)})
        return sorted(output, key=lambda hit: (-hit["rerank_score"], hit["chunk_id"]))
