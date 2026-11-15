"""集中读取配置；默认使用本地文件和离线检索。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import KnowledgeError

SOURCE_ROOT = Path(__file__).resolve().parents[2]
# 源码运行定位到仓库；wheel 安装后使用工作目录，避免写入 site-packages。
PROJECT_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "pyproject.toml").is_file() else Path.cwd()


def load_env(path: Path) -> None:
    """读取简单 KEY=VALUE 文件；已有环境变量优先，不执行插值或命令。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("BIOKB_"):
            os.environ.setdefault(name, value.strip().strip("\"'"))


def integer(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise KnowledgeError(f"配置 {name} 必须为整数") from exc
    if not lower <= value <= upper:
        raise KnowledgeError(f"配置 {name} 必须介于 {lower} 与 {upper} 之间")
    return value


def validate_url(url: str, name: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise KnowledgeError(f"{name} 必须为不含凭据、查询参数和片段的 HTTP(S) 地址")


def secret(name: str) -> str:
    """优先专用密钥，也可显式引用系统变量；不把密钥复制进配置文件。"""
    import re

    direct = os.environ.get(name, "")
    if direct:
        return direct
    reference = os.environ.get(name + "_ENV", "")
    if not reference:
        return ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", reference):
        raise KnowledgeError(f"{name}_ENV 必须为有效环境变量名")
    if os.environ.get(reference):
        return os.environ[reference]
    if os.name == "nt":
        import winreg

        for hive, path in [
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ]:
            try:
                with winreg.OpenKey(hive, path) as handle:
                    value, _ = winreg.QueryValueEx(handle, reference)
                if value:
                    return str(value)
            except OSError:
                continue
    return ""


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "biokb")
    host: str = "127.0.0.1"
    port: int = 5000
    chunk_size: int = 800
    chunk_overlap: int = 120
    max_upload_bytes: int = 25 * 1024 * 1024
    max_text_chars: int = 2_000_000
    embedding_backend: str = "hash"
    embedding_model: str = "local-hash-v2"
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_api_key: str = field(default="", repr=False)
    embedding_dimensions: int = 384
    llm_backend: str = "disabled"
    llm_model: str = ""
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_api_key: str = field(default="", repr=False)
    api_token: str = field(default="", repr=False)
    request_timeout: int = 60
    max_context_chars: int = 24000
    default_retrieval: str = "hybrid"
    top_k: int = 5
    workers: int = 2
    retrieval_candidates: int = 50
    rrf_constant: int = 60
    dense_min_score: float = 0.12
    max_evidence_per_document: int = 2
    reranker_backend: str = "disabled"
    reranker_model: str = ""
    reranker_base_url: str = "http://127.0.0.1:11434"
    reranker_api_key: str = field(default="", repr=False)
    embedding_revision: str = ""
    llm_response_format: str = "text"
    llm_max_tokens: int = 1600
    llm_output_mode: str = "answer"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "knowledge.sqlite3"

    def validate(self) -> "Settings":
        bounds = {
            "port": (1, 65535),
            "chunk_size": (200, 4000),
            "chunk_overlap": (0, 3999),
            "embedding_dimensions": (64, 16384),
            "request_timeout": (1, 300),
            "top_k": (1, 20),
            "workers": (1, 8),
            "max_upload_bytes": (1, 25 * 1024 * 1024),
            "max_text_chars": (10, 2_000_000),
            "max_context_chars": (1000, 100000),
            "retrieval_candidates": (1, 50),
            "rrf_constant": (1, 1000),
            "max_evidence_per_document": (1, 50),
            "llm_max_tokens": (128, 4096),
        }
        for name, (lower, upper) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise KnowledgeError(f"配置 {name} 必须为 {lower}–{upper} 的整数")
        if self.embedding_backend not in {"hash", "ollama", "openai_compatible"}:
            raise KnowledgeError("BIOKB_EMBEDDING_BACKEND 仅支持 hash、ollama、openai_compatible")
        if self.llm_backend not in {"disabled", "ollama", "openai_compatible"}:
            raise KnowledgeError("BIOKB_LLM_BACKEND 仅支持 disabled、ollama、openai_compatible")
        if self.llm_response_format not in {"text", "json_object", "json_schema"}:
            raise KnowledgeError("生成响应格式仅支持 text、json_object、json_schema")
        if self.llm_output_mode not in {"answer", "claims"}:
            raise KnowledgeError("生成输出模式仅支持 answer 或 claims")
        if (
            self.llm_output_mode == "claims"
            and self.llm_backend == "openai_compatible"
            and self.llm_response_format != "json_schema"
        ):
            raise KnowledgeError("结构化论断模式需要服务支持并启用 json_schema")
        if self.default_retrieval not in {"bm25", "dense", "hybrid", "hybrid_rerank"}:
            raise KnowledgeError("检索策略仅支持 bm25、dense、hybrid、hybrid_rerank")
        if self.reranker_backend not in {"disabled", "compatible"}:
            raise KnowledgeError("重排序后端仅支持 disabled、compatible")
        if self.reranker_backend != "disabled" and not self.reranker_model:
            raise KnowledgeError("启用重排序必须配置模型名称")
        if (
            isinstance(self.dense_min_score, bool)
            or not isinstance(self.dense_min_score, (int, float))
            or not -1 <= self.dense_min_score <= 1
        ):
            raise KnowledgeError("向量最低分必须在 -1 到 1 之间")
        validate_url(self.reranker_base_url, "重排序服务地址")
        if not 200 <= self.chunk_size <= 4000 or not 0 <= self.chunk_overlap < self.chunk_size:
            raise KnowledgeError("文本块长度必须在 200–4000 之间，重叠长度必须小于块长度")
        if self.llm_backend != "disabled" and not self.llm_model:
            raise KnowledgeError("启用生成模型后必须配置 BIOKB_LLM_MODEL")
        if self.embedding_backend != "hash" and self.embedding_model == "local-hash-v2":
            raise KnowledgeError("启用向量模型后必须配置 BIOKB_EMBEDDING_MODEL")
        validate_url(self.embedding_base_url, "向量服务地址")
        validate_url(self.llm_base_url, "语言模型服务地址")
        if self.host not in {"127.0.0.1", "localhost", "::1"} and not self.api_token:
            raise KnowledgeError("对外监听需设置 BIOKB_API_TOKEN；本地使用请保留 127.0.0.1")
        return self

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        base = (root or PROJECT_ROOT).resolve()
        load_env(base / ".env")
        data = Path(os.environ.get("BIOKB_DATA_DIR", "data/biokb")).expanduser()
        return cls(
            data_dir=(data if data.is_absolute() else base / data).resolve(),
            host=os.environ.get("BIOKB_HOST", "127.0.0.1"),
            port=integer("BIOKB_PORT", 5000, 1, 65535),
            chunk_size=integer("BIOKB_CHUNK_SIZE", 800, 200, 4000),
            chunk_overlap=integer("BIOKB_CHUNK_OVERLAP", 120, 0, 2000),
            embedding_backend=os.environ.get("BIOKB_EMBEDDING_BACKEND", "hash"),
            embedding_model=os.environ.get("BIOKB_EMBEDDING_MODEL", "local-hash-v2"),
            embedding_base_url=os.environ.get("BIOKB_EMBEDDING_BASE_URL", "http://127.0.0.1:11434"),
            embedding_api_key=secret("BIOKB_EMBEDDING_API_KEY"),
            embedding_dimensions=integer("BIOKB_EMBEDDING_DIMENSIONS", 384, 64, 16384),
            llm_backend=os.environ.get("BIOKB_LLM_BACKEND", "disabled"),
            llm_model=os.environ.get("BIOKB_LLM_MODEL", ""),
            llm_base_url=os.environ.get("BIOKB_LLM_BASE_URL", "http://127.0.0.1:11434"),
            llm_api_key=secret("BIOKB_LLM_API_KEY"),
            api_token=os.environ.get("BIOKB_API_TOKEN", ""),
            request_timeout=integer("BIOKB_REQUEST_TIMEOUT", 60, 1, 300),
            max_context_chars=integer("BIOKB_MAX_CONTEXT_CHARS", 24000, 1000, 100000),
            default_retrieval=os.environ.get("BIOKB_RETRIEVAL", "hybrid"),
            top_k=integer("BIOKB_TOP_K", 5, 1, 20),
            workers=integer("BIOKB_WORKERS", 2, 1, 8),
            retrieval_candidates=integer("BIOKB_RETRIEVAL_CANDIDATES", 50, 1, 50),
            rrf_constant=integer("BIOKB_RRF_CONSTANT", 60, 1, 1000),
            dense_min_score=decimal("BIOKB_DENSE_MIN_SCORE", 0.12),
            max_evidence_per_document=integer("BIOKB_MAX_EVIDENCE_PER_DOCUMENT", 2, 1, 50),
            reranker_backend=os.environ.get("BIOKB_RERANKER_BACKEND", "disabled"),
            reranker_model=os.environ.get("BIOKB_RERANKER_MODEL", ""),
            reranker_base_url=os.environ.get("BIOKB_RERANKER_BASE_URL", "http://127.0.0.1:11434"),
            reranker_api_key=secret("BIOKB_RERANKER_API_KEY"),
            embedding_revision=os.environ.get("BIOKB_EMBEDDING_REVISION", ""),
            llm_response_format=os.environ.get("BIOKB_LLM_RESPONSE_FORMAT", "text"),
            llm_max_tokens=integer("BIOKB_LLM_MAX_TOKENS", 1600, 128, 4096),
            llm_output_mode=os.environ.get("BIOKB_LLM_OUTPUT_MODE", "answer"),
        ).validate()

    def public(self) -> dict:
        return {
            "embedding_backend": self.embedding_backend,
            "embedding_model": self.embedding_model,
            "embedding_key_configured": bool(self.embedding_api_key),
            "llm_backend": self.llm_backend,
            "llm_model": self.llm_model,
            "llm_key_configured": bool(self.llm_api_key),
            "retrieval": self.default_retrieval,
            "top_k": self.top_k,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "request_timeout": self.request_timeout,
            "max_context_chars": self.max_context_chars,
            "authentication_required": bool(self.api_token),
            "answer_mode": "extractive" if self.llm_backend == "disabled" else "generated",
            "retrieval_candidates": self.retrieval_candidates,
            "rrf_constant": self.rrf_constant,
            "dense_min_score": self.dense_min_score,
            "max_evidence_per_document": self.max_evidence_per_document,
            "reranker_backend": self.reranker_backend,
            "reranker_model": self.reranker_model,
            "embedding_revision": self.embedding_revision,
            "llm_response_format": self.llm_response_format,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_output_mode": self.llm_output_mode,
        }


def decimal(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise KnowledgeError(f"配置 {name} 必须为数值") from exc
