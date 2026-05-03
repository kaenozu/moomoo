"""Retry utilities for transient failures.

Purpose: Centralize retry logic with exponential backoff for broker API calls.
Related: broker/opend.py, broker/paper.py, notify.py.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retry configurations
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_DELAY = 30.0

# Transient exceptions that should be retried
TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    RuntimeError,
    OSError,
)


def with_retries(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    max_delay: float = DEFAULT_MAX_DELAY,
    exceptions: tuple[type[Exception], ...] = TRANSIENT_EXCEPTIONS,
    retry_on_result: Callable[[T], bool] | None = None,
    logger_extra: dict | None = None,
    raise_on_failure: type[Exception] | None = None,
):
    """Decorator to retry a function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (excluding initial call)
        base_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each failure
        max_delay: Maximum delay between retries
        exceptions: Exception types to catch and retry on
        retry_on_result: Optional predicate to determine if result should be retried
        logger_extra: Optional dict to include in log records
        raise_on_failure: Optional exception class to raise when all retries exhausted.
                        If None, the last caught exception is re-raised.

    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            delay = base_delay

            for attempt in range(1, max_retries + 2):  # +2: initial + retries
                try:
                    result = func(*args, **kwargs)
                    if retry_on_result is not None and retry_on_result(result):
                        if attempt <= max_retries + 1:
                            logger.warning(
                                "Retry triggered by result (attempt %d/%d)",
                                attempt,
                                max_retries + 1,
                                extra=logger_extra or {},
                            )
                            time.sleep(min(delay, max_delay))
                            delay *= backoff_factor
                            continue
                    return result
                except exceptions as exc:
                    last_exc = exc
                    if attempt <= max_retries + 1:
                        logger.warning(
                            "%s attempt %d/%d failed: %s; retrying in %.1fs",
                            func.__name__,
                            attempt,
                            max_retries + 1,
                            exc,
                            min(delay, max_delay),
                            extra=logger_extra or {},
                        )
                        time.sleep(min(delay, max_delay))
                        delay *= backoff_factor
                        continue
                    # Exhausted retries
                    logger.error(
                        "%s failed after %d attempts: %s",
                        func.__name__,
                        attempt,
                        exc,
                        extra=logger_extra or {},
                    )
                    if raise_on_failure is not None:
                        raise raise_on_failure(
                            f"{func.__name__} failed after {attempt} attempts: {exc}"
                        ) from exc
                    raise

            # Should not reach here, but just in case
            if last_exc:
                if raise_on_failure is not None:
                    raise raise_on_failure(
                        f"{func.__name__} failed: {last_exc}"
                    ) from last_exc
                raise last_exc
            return None  # type: ignore[unreachable]

        return wrapper

    return decorator


def with_retry_context(
    context_name: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
):
    """Context manager for retry loops with standardized logging.

    Args:
        context_name: Name to include in log messages
        max_retries: Maximum retry attempts
        base_delay: Initial delay seconds
        backoff_factor: Exponential backoff multiplier
    """
    from contextlib import contextmanager

    @contextmanager
    def retry_context(*, logger_extra: dict | None = None):
        delay = base_delay
        last_exc = None

        for attempt in range(1, max_retries + 2):
            try:
                yield
                return
            except TRANSIENT_EXCEPTIONS as exc:
                last_exc = exc
                if attempt <= max_retries + 1:
                    logger.warning(
                        "%s attempt %d/%d failed: %s; retrying in %.1fs",
                        context_name,
                        attempt,
                        max_retries + 1,
                        exc,
                        min(delay, max_delay := 30.0),
                        extra=logger_extra or {},
                    )
                    time.sleep(min(delay, max_delay))
                    delay *= backoff_factor
                    continue
                logger.error(
                    "%s failed after %d attempts: %s",
                    context_name,
                    attempt,
                    exc,
                    extra=logger_extra or {},
                )
                raise

        if last_exc:
            raise last_exc

    return retry_context()
