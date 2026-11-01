"""Content-free timing for Legacy calculation stages."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from time import monotonic
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
LOGGER = logging.getLogger("uvicorn.error")


def timed_stage(stage: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def measured(*args: P.args, **kwargs: P.kwargs) -> T:
            start = monotonic()
            outcome = "failed"
            LOGGER.info("calculation_stage=%s event=start", stage)
            try:
                result = function(*args, **kwargs)
                outcome = "succeeded"
                return result
            finally:
                LOGGER.info(
                    "calculation_stage=%s event=end outcome=%s duration_seconds=%.3f",
                    stage,
                    outcome,
                    monotonic() - start,
                )

        return measured

    return decorate
