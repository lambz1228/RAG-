"""进程内有界任务队列，适用于单机毕业设计演示。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Callable

from .errors import ConflictError, KnowledgeError, NotFoundError

logger = logging.getLogger(__name__)


class JobManager:
    def __init__(self, workers: int = 2, capacity: int = 8, repository=None):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="biokb")
        self.capacity = capacity
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.repository = repository
        self._closed = False
        if repository:
            for job in repository.load_jobs():
                if job["status"] in {"queued", "running"}:
                    job.update(
                        status="interrupted",
                        stage="服务中断",
                        error="服务已重启，请重新提交任务",
                        code="interrupted",
                    )
                    repository.save_job(job)
                self._jobs[job["id"]] = job

    def _persist(self, job_id: str) -> None:
        if self.repository:
            self.repository.save_job(self._jobs[job_id])

    def submit(self, kind: str, function: Callable, retry: dict | None = None) -> dict:
        with self._lock:
            if self._closed:
                raise ConflictError("任务队列已关闭")
            active = sum(job["status"] in {"queued", "running"} for job in self._jobs.values())
            if active >= self.capacity:
                raise ConflictError("任务队列已满，请等待当前任务完成")
            for key in sorted(self._jobs, key=lambda item: self._jobs[item]["created_at"]):
                if len(self._jobs) < 100:
                    break
                if self._jobs[key]["status"] not in {"queued", "running"}:
                    del self._jobs[key]
            job_id = "job_" + uuid.uuid4().hex
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "stage": "排队中",
                "progress": 0,
                "created_at": time.time(),
                "retry": retry,
            }
            initial = deepcopy(self._jobs[job_id])
            self._persist(job_id)
            if self.repository:
                self.repository.prune_jobs(set(self._jobs))
            self.executor.submit(self._run, job_id, function)
        return initial

    def _run(self, job_id: str, function: Callable) -> None:
        def progress(stage: str, value: int) -> None:
            with self._lock:
                self._jobs[job_id].update(
                    status="running", stage=stage, progress=max(0, min(100, int(value)))
                )
                self._persist(job_id)

        progress("开始处理", 1)
        try:
            result = function(progress)
            with self._lock:
                self._jobs[job_id].update(
                    status="completed", stage="完成", progress=100, result=result
                )
                self._persist(job_id)
        except KnowledgeError as exc:
            with self._lock:
                self._jobs[job_id].update(
                    status="failed", stage="处理失败", error=str(exc), code=exc.code
                )
                self._persist(job_id)
        except Exception:
            # 不记录模型响应、请求正文或异常字符串，防止敏感材料进入日志。
            logger.error("任务异常，任务编号：%s", job_id)
            with self._lock:
                self._jobs[job_id].update(
                    status="failed",
                    stage="处理失败",
                    error="任务执行异常，请检查文档格式或本地存储状态",
                    code="internal_error",
                )
                self._persist(job_id)

    def get(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise NotFoundError("任务不存在或已超过最近 100 条保留范围")
            return deepcopy(self._jobs[job_id])

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)

    def recent(self) -> list[dict]:
        with self._lock:
            return [
                {key: value for key, value in job.items() if key != "result"}
                for job in sorted(
                    self._jobs.values(), key=lambda job: job["created_at"], reverse=True
                )[:30]
            ]
