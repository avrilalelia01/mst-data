"""Shared utilities for the Shopee Sales Analyzer.

Contains the retry_with_backoff decorator used across the auth client, data
collector, and data store layers.

Requirements: 1.5, 9.4
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, Tuple, Type

logger = logging.getLogger(__name__)


def retry_with_backoff(
    max_attempts: int = 3,
    delay_seconds: float = 5.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
) -> Callable:
    """Decorator that retries the wrapped function on specified exceptions.

    Args:
        max_attempts: Total number of attempts (including the first call).
                      Must be >= 1.
        delay_seconds: Fixed wait time in seconds between consecutive attempts.
        exceptions: Tuple of exception types that trigger a retry.  Any
                    exception NOT in this tuple is re-raised immediately.

    Returns:
        A decorator that wraps an ordinary function with retry logic.

    Example::

        @retry_with_backoff(max_attempts=3, delay_seconds=5,
                            exceptions=(ConnectionError, TimeoutError))
        def call_shopee_api(...):
            ...

    Requirements: 1.5 (auth retries 3×/5s), 9.4 (storage retries 3×/2s)
    """

    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt < max_attempts:
                        logger.warning(
                            "Attempt %d/%d for %s failed: %s. "
                            "Retrying in %.1f second(s).",
                            attempt,
                            max_attempts,
                            func.__qualname__,
                            exc,
                            delay_seconds,
                        )
                        time.sleep(delay_seconds)
                    else:
                        logger.error(
                            "All %d attempt(s) for %s exhausted. Last error: %s",
                            max_attempts,
                            func.__qualname__,
                            exc,
                        )
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
