"""Мост между рабочими потоками и интерфейсом: сигналы Qt безопасно доставляются в главный поток."""
from __future__ import annotations

import itertools
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    progress = Signal(int, object)        # job_id, ProgressEvent
    log = Signal(int, str)
    job_event = Signal(str, int)          # событие, job_id
    bg_done = Signal(int, object, object) # id колбэка, результат, ошибка
    url_info = Signal(str, object, object)  # url, info|None, error|None

    def __init__(self) -> None:
        super().__init__()
        self._cbs: dict[int, Callable[[Any, Exception | None], None]] = {}
        self._ids = itertools.count(1)
        self.bg_done.connect(self._on_bg_done)

    def run_bg(self, fn: Callable[[], Any], cb: Callable[[Any, Exception | None], None] | None = None) -> None:
        """Выполняет fn в фоновом потоке, cb(result, error) вызывается в потоке интерфейса."""
        cid = next(self._ids)
        if cb:
            self._cbs[cid] = cb

        def work() -> None:
            try:
                res, err = fn(), None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self.bg_done.emit(cid, res, err)

        threading.Thread(target=work, daemon=True).start()

    def _on_bg_done(self, cid: int, res: Any, err: Any) -> None:
        cb = self._cbs.pop(cid, None)
        if cb:
            cb(res, err)
