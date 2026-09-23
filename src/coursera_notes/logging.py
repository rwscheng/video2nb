"""Small plain-text CLI logging setup."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

LOGGER_NAME = "coursera_notes"


class _CurrentStderrHandler(logging.StreamHandler[TextIO]):
    """Resolve stderr at emit time so repeated CLI invocations remain usable."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def configure_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    level = logging.ERROR if quiet else logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger(LOGGER_NAME)
    root_logger.handlers.clear()
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    root_logger.propagate = False
