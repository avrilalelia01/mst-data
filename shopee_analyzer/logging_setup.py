"""Logging setup and structured logging helpers for the Shopee Sales Analyzer.

Provides utilities for configuring the application logger and emitting
structured log entries, including the per-run summary required by
Requirement 9.5.

Requirements: 9.1, 9.3, 9.5
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore[no-redef]

from shopee_analyzer.models import Configuration


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

def configure_logging(config: Configuration) -> None:
    """Configure the root logger with JSON-formatted file and stream handlers.

    Sets the root logger level from ``config.log_level``, attaches a
    :class:`logging.FileHandler` writing JSON lines to ``config.log_file``,
    and a :class:`logging.StreamHandler` writing to stdout.

    Calling this function a second time replaces all existing handlers on the
    root logger (no duplicates).

    Requirements: 9.1, 9.3

    Parameters
    ----------
    config:
        Validated :class:`Configuration` object.  ``partner_key`` is never
        written to any log output.
    """
    level_map: dict[str, int] = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    level = level_map.get(config.log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()

    # Remove all existing handlers to avoid accumulation on re-configure.
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(level)

    # JSON formatter — produces one JSON object per line.
    # Note: %f is not supported by time.strftime on all platforms; use %S only.
    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    # File handler.
    file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Stream (console) handler.
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# Structured per-operation logging
# ---------------------------------------------------------------------------

def log_operation(
    logger: logging.Logger,
    *,
    operation: str,
    shop_id: int,
    message: str,
    **kwargs: Any,
) -> None:
    """Emit a structured INFO log entry for a named operation.

    Always includes the ``operation``, ``shop_id``, and ``message`` fields.
    Additional keyword arguments are forwarded as extra structured fields.

    Parameters
    ----------
    logger:
        The :class:`logging.Logger` instance to write to.
    operation:
        Human-readable name of the operation, e.g. ``"data_collection"``.
    shop_id:
        The shop ID associated with this operation.
    message:
        Free-text description of the operation outcome.
    **kwargs:
        Additional key/value pairs to include in the structured log entry,
        e.g. ``records_collected=1420``, ``duration_seconds=42.7``.

    Requirements: 9.1
    """
    extra: dict[str, Any] = {
        "operation": operation,
        "shop_id": shop_id,
        **kwargs,
    }
    logger.info(message, extra=extra)


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def log_run_summary(
    logger: logging.Logger,
    operations_completed: int,
    operations_failed: int,
    total_records_processed: int,
    total_execution_time_seconds: float,
) -> None:
    """Emit a structured run-summary log entry at INFO level.

    Produces exactly one log record containing all four required fields
    from Requirement 9.5:
      - operations_completed
      - operations_failed
      - total_records_processed
      - total_execution_time_seconds

    Parameters
    ----------
    logger:
        The :class:`logging.Logger` instance to write the summary to.
    operations_completed:
        Number of operations that completed successfully in this run.
    operations_failed:
        Number of operations that failed in this run.
    total_records_processed:
        Total number of records processed across all operations.
    total_execution_time_seconds:
        Wall-clock time for the entire execution run in seconds.

    Requirements: 9.5
    """
    logger.info(
        "Run complete",
        extra={
            "operations_completed": operations_completed,
            "operations_failed": operations_failed,
            "total_records_processed": total_records_processed,
            "total_execution_time_seconds": total_execution_time_seconds,
        },
    )
