"""RAG 检索评估的公共工具：语料加载、密钥读取、云端模型调用。"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_DIR = ROOT / "sci-illust-system"
GRAPHRAG_ROOT = SYSTEM_DIR / "web_app" / "data" / "text_kb" / "biology" / "graphrag"
EVAL_DIR = ROOT / "tests" / "benchmarks" / "rag_eval"
API_BASE = "https://api.siliconflow.cn/v1"

if str(SYSTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEM_DIR))


def api_key() -> str:
    """复用项目的密钥查找逻辑：环境变量 khyt_key 等，Windows 下也读注册表。"""
    from text_kb.graphrag_manager import GraphRAGTextKBManager

    keys = GraphRAGTextKBManager(base_dir=str(GRAPHRAG_ROOT.parent.parent))._api_key_fallbacks()
    key = keys.get("GRAPHRAG_API_KEY", "")
    if not key:
        raise SystemExit("未找到 API 密钥（系统变量 khyt_key）")
    return key


def load_corpus() -> list[dict]:
    """读取 GraphRAG 输入目录中的全部文档，doc_id 为文件名（不含扩展名）。"""
    docs = []
    for path in sorted((GRAPHRAG_ROOT / "input").glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        title = text.split("\n", 1)[0].strip()
        docs.append({"doc_id": path.stem, "title": title, "text": text})
    return docs


def _post(path: str, payload: dict, timeout: int = 120, retries: int = 5) -> dict:
    body = json.dumps(payload).encode("utf-8")
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API_BASE + path,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key()},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            last = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code not in (429, 500, 502, 503, 504):
                raise last
        except Exception as exc:  # 网络抖动重试
            last = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败: {last}")


def chat(messages: list[dict], model: str, max_tokens: int = 800, temperature: float = 0.0) -> str:
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if "Qwen3" in model:
        payload["enable_thinking"] = False
    data = _post("/chat/completions", payload)
    return (data["choices"][0]["message"].get("content") or "").strip()


def embed(texts: list[str], model: str, batch: int = 16) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch):
        data = _post("/embeddings", {"model": model, "input": texts[i : i + batch]})
        rows = sorted(data["data"], key=lambda r: r["index"])
        vectors.extend(r["embedding"] for r in rows)
    return vectors


def parse_json_block(text: str):
    """从模型输出中取出第一个 JSON 数组或对象；小模型常漏逗号，解析失败时补一次。"""
    match = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    if not match:
        raise ValueError("未找到 JSON")
    raw = match.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'"\s*\n\s*"', '",\n"', raw))
