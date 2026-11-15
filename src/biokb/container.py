"""应用依赖装配，支持测试注入模型和向量实现。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .ingestion import IngestionService
from .jobs import JobManager
from .providers import EmbeddingProvider, LanguageModel
from .rag import RAGService
from .retrieval import RetrievalService
from .storage import Repository


@dataclass
class Services:
    settings: Settings
    repository: Repository
    ingestion: IngestionService
    retrieval: RetrievalService
    rag: RAGService
    jobs: JobManager


def build_services(settings: Settings, embedder=None, model=None) -> Services:
    settings.validate()
    repository = Repository(settings.database_path)
    embedder = embedder or EmbeddingProvider(settings)
    model = model or LanguageModel(settings)
    retrieval = RetrievalService(repository, embedder, settings)
    return Services(
        settings,
        repository,
        IngestionService(repository, embedder, settings),
        retrieval,
        RAGService(repository, retrieval, model, settings),
        JobManager(settings.workers, repository=repository),
    )
