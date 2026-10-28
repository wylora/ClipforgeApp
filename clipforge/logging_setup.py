from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import Callable

from .config import subdir

_configured = False


class CallbackHandler(logging.Handler):
    def __init__(self, cb: Callable[[str, str], None]):
        super().__init__(logging.INFO)
        self.cb = cb

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.cb(record.levelname, self.format(record))
        except Exception:
            pass


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger("clipforge")
    if _configured:
        return root
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        fh = RotatingFileHandler(subdir("logs") / "clipforge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    _configured = True
    return root


def add_callback_handler(cb: Callable[[str, str], None]) -> CallbackHandler:
    h = CallbackHandler(cb)
    h.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("clipforge").addHandler(h)
    return h
