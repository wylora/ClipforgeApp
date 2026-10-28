"""Сервисы приложения: настройки, БД, менеджер задач. Не зависит от Qt-виджетов."""
from __future__ import annotations

import logging
from pathlib import Path

from .config import Settings
from .db import Database
from .ffmpeg_utils import set_ffmpeg_dir
from .pipeline import Pipeline
from .tasks import Job, JobManager

log = logging.getLogger("clipforge.app")


class Services:
    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self.settings = Settings.load()
        set_ffmpeg_dir(self.settings.ui.ffmpeg_dir)
        self.db = Database()
        for p in self.db.list_projects():                 # прерванные запуском приложения проекты
            if p["status"] in ("running", "queued"):
                self.db.update_project(p["id"], status="interrupted", message="Обработка была прервана — можно продолжить.")
        self.manager = JobManager(self._run, self.settings.analysis.parallel_jobs, 20, self._on_event)

    def _run(self, job: Job):
        b = self.bridge
        return Pipeline(self.db, job.settings, job.source, job.token,
                        on_progress=lambda e: b.progress.emit(job.id, e),
                        on_log=lambda m: b.log.emit(job.id, m), project_id=job.project_id).run()

    def _on_event(self, ev: str, job: Job) -> None:
        self.bridge.job_event.emit(ev, job.id)

    def submit(self, source: str, settings: Settings | None = None, project_id: int | None = None) -> Job:
        job = Job(source, (settings or self.settings).copy(), project_id)
        self.manager.max_parallel = max(1, min(self.settings.analysis.parallel_jobs, 3))
        return self.manager.submit(job)

    def job(self, job_id: int) -> Job | None:
        return next((j for j in self.manager.jobs if j.id == job_id), None)

    def save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError as e:
            log.warning("Не удалось сохранить настройки: %s", e)

    def shutdown(self) -> None:
        self.manager.shutdown()
        self.save_settings()
        self.db.close()
