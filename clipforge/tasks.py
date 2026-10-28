"""Очередь задач: ограничение размера и параллелизма, защита от дублей, отмена."""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Settings
from .errors import CancelledError, ClipForgeError, NoGoodMomentsError
from .util import CancelToken

log = logging.getLogger("clipforge.tasks")
_ids = itertools.count(1)


class QueueFullError(ClipForgeError): ...
class DuplicateJobError(ClipForgeError): ...


@dataclass
class Job:
    source: str
    settings: Settings
    project_id: int | None = None
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"          # queued running done error cancelled no_clips
    token: CancelToken = field(default_factory=CancelToken)
    result: Any = None
    error: str = ""
    created: float = field(default_factory=time.time)

    @property
    def key(self) -> tuple:
        return (self.source, self.settings.clip.mode, self.settings.clip.custom_prompt)


class JobManager:
    def __init__(self, runner: Callable[[Job], Any], max_parallel: int = 1, max_queue: int = 20,
                 on_event: Callable[[str, Job], None] | None = None):
        self.runner = runner
        self.max_parallel = max(1, min(max_parallel, 3))
        self.max_queue = max_queue
        self.on_event = on_event or (lambda ev, job: None)
        self._q: queue.Queue[Job | None] = queue.Queue()
        self._jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stop = False

    @property
    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def submit(self, job: Job) -> Job:
        with self._lock:
            active = [j for j in self._jobs.values() if j.status in ("queued", "running")]
            if any(j.key == job.key for j in active):
                raise DuplicateJobError("Это видео уже обрабатывается или стоит в очереди с теми же настройками.")
            if sum(1 for j in active if j.status == "queued") >= self.max_queue:
                raise QueueFullError(f"Очередь заполнена (максимум {self.max_queue} задач).",
                                     hint="Дождитесь завершения части задач.")
            self._jobs[job.id] = job
            while len(self._threads) < self.max_parallel:
                t = threading.Thread(target=self._worker, name=f"cf-worker-{len(self._threads)}", daemon=True)
                self._threads.append(t)
                t.start()
        self._q.put(job)
        self.on_event("queued", job)
        return job

    def _worker(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            if job.token.cancelled or job.status == "cancelled":
                job.status = "cancelled"
                self.on_event("cancelled", job)
                continue
            job.status = "running"
            self.on_event("started", job)
            try:
                job.result = self.runner(job)
                job.status = "done"
                self.on_event("finished", job)
            except CancelledError:
                job.status = "cancelled"
                self.on_event("cancelled", job)
            except NoGoodMomentsError as e:
                job.status, job.error = "no_clips", e.user_text()
                self.on_event("no_clips", job)
            except ClipForgeError as e:
                job.status, job.error = "error", e.user_text()
                log.error("Задача %s: %s %s", job.id, e.message, e.details)
                self.on_event("failed", job)
            except Exception as e:  # непредвиденная ошибка — не роняем воркер
                log.exception("Необработанная ошибка в задаче %s", job.id)
                job.status, job.error = "error", f"Непредвиденная ошибка: {e}"
                self.on_event("failed", job)

    def cancel(self, job_id: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.token.cancel()
            if job.status == "queued":
                job.status = "cancelled"

    def cancel_all(self) -> None:
        for j in self.jobs:
            self.cancel(j.id)

    def shutdown(self, wait: float = 3.0) -> None:
        self.cancel_all()
        for _ in self._threads:
            self._q.put(None)
        end = time.time() + wait
        for t in self._threads:
            t.join(timeout=max(0.0, end - time.time()))
