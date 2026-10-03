"""Unit tests for shopee_analyzer.logging_setup.

Tests cover:
- configure_logging sets the correct log level on the root logger
- configure_logging attaches a file handler and stream handler
- log_operation writes an INFO entry with structured extra fields
- log_run_summary writes an INFO entry with all four summary fields
- partner_key never appears in log output
- Property 20: Structured log entry completeness (Requirements 9.1)
- Property 21: Run summary completeness (Requirements 9.5)

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shopee_analyzer.logging_setup import (
    configure_logging,
    log_operation,
    log_run_summary,
)
from shopee_analyzer.models import Configuration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    log_level: str = "INFO",
    log_file: str | None = None,
) -> Configuration:
    """Return a minimal Configuration for testing."""
    if log_file is None:
        # Use a temp file so tests are isolated
        tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        tmp.close()
        log_file = tmp.name
    return Configuration(
        partner_id=12345,
        partner_key="secret-key-never-logged",
        shop_id=99999,
        data_store_path="./data/test.db",
        output_dir="./output",
        refresh_schedule="daily",
        default_period_days=30,
        log_level=log_level,  # type: ignore[arg-type]
        log_file=log_file,
    )


def _read_log_lines(path: str) -> list[dict]:
    """Read a JSON-per-line log file and return parsed dicts."""
    lines = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                lines.append(json.loads(line))
    return lines


@pytest.fixture(autouse=True)
def reset_root_logger():
    """Tear down root-logger handlers after every test to avoid cross-test pollution."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


# ---------------------------------------------------------------------------
# configure_logging tests
# ---------------------------------------------------------------------------

class TestConfigureLogging:
    def test_sets_debug_level(self, tmp_path):
        config = _make_config(log_level="DEBUG", log_file=str(tmp_path / "debug.log"))
        configure_logging(config)
        assert logging.getLogger().level == logging.DEBUG

    def test_sets_info_level(self, tmp_path):
        config = _make_config(log_level="INFO", log_file=str(tmp_path / "info.log"))
        configure_logging(config)
        assert logging.getLogger().level == logging.INFO

    def test_sets_warn_level(self, tmp_path):
        config = _make_config(log_level="WARN", log_file=str(tmp_path / "warn.log"))
        configure_logging(config)
        assert logging.getLogger().level == logging.WARNING

    def test_sets_error_level(self, tmp_path):
        config = _make_config(log_level="ERROR", log_file=str(tmp_path / "error.log"))
        configure_logging(config)
        assert logging.getLogger().level == logging.ERROR

    def test_attaches_file_handler(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)
        file_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) >= 1

    def test_attaches_stream_handler(self, tmp_path):
        config = _make_config(log_file=str(tmp_path / "test.log"))
        configure_logging(config)
        stream_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) >= 1

    def test_creates_log_file(self, tmp_path):
        log_path = str(tmp_path / "created.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)
        # Emit a message so the file is flushed
        logging.getLogger().info("init")
        assert Path(log_path).exists()

    def test_reconfigure_replaces_handlers(self, tmp_path):
        """Calling configure_logging twice should not accumulate duplicate handlers."""
        config = _make_config(log_file=str(tmp_path / "test.log"))
        configure_logging(config)
        handler_count_first = len(logging.getLogger().handlers)
        configure_logging(config)
        handler_count_second = len(logging.getLogger().handlers)
        assert handler_count_second == handler_count_first


# ---------------------------------------------------------------------------
# log_operation tests
# ---------------------------------------------------------------------------

class TestLogOperation:
    def test_writes_info_entry(self, tmp_path):
        log_path = str(tmp_path / "ops.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.ops")
        log_operation(logger, operation="data_collection", shop_id=12345, message="Collection complete")

        # Flush handlers
        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert len(entries) >= 1

    def test_includes_operation_field(self, tmp_path):
        log_path = str(tmp_path / "ops.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.ops")
        log_operation(logger, operation="analysis", shop_id=12345, message="Analysis done")

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any(e.get("operation") == "analysis" for e in entries)

    def test_includes_shop_id_field(self, tmp_path):
        log_path = str(tmp_path / "ops.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.ops")
        log_operation(logger, operation="report_export", shop_id=99999, message="Export done")

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any(e.get("shop_id") == 99999 for e in entries)

    def test_includes_message_field(self, tmp_path):
        log_path = str(tmp_path / "ops.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.ops")
        log_operation(logger, operation="data_collection", shop_id=1, message="Unique message xyz")

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any("Unique message xyz" in e.get("message", "") for e in entries)

    def test_includes_extra_kwargs(self, tmp_path):
        log_path = str(tmp_path / "ops.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.ops")
        log_operation(
            logger,
            operation="data_collection",
            shop_id=12345,
            message="Done",
            records_collected=1420,
            duration_seconds=42.7,
        )

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        matching = [e for e in entries if e.get("operation") == "data_collection"]
        assert len(matching) >= 1
        entry = matching[0]
        assert entry.get("records_collected") == 1420
        assert entry.get("duration_seconds") == pytest.approx(42.7, rel=1e-3)

    def test_partner_key_not_in_log(self, tmp_path):
        """partner_key must never appear in log output."""
        log_path = str(tmp_path / "ops.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.ops")
        log_operation(logger, operation="data_collection", shop_id=12345, message="Running")

        for h in logging.getLogger().handlers:
            h.flush()

        raw = Path(log_path).read_text(encoding="utf-8")
        assert "secret-key-never-logged" not in raw


# ---------------------------------------------------------------------------
# log_run_summary tests
# ---------------------------------------------------------------------------

class TestLogRunSummary:
    def test_writes_run_summary_entry(self, tmp_path):
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(
            logger,
            operations_completed=4,
            operations_failed=1,
            total_records_processed=5000,
            total_execution_time_seconds=120.5,
        )

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert len(entries) >= 1

    def test_includes_operations_completed(self, tmp_path):
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(logger, operations_completed=3, operations_failed=0,
                        total_records_processed=100, total_execution_time_seconds=10.0)

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any(e.get("operations_completed") == 3 for e in entries)

    def test_includes_operations_failed(self, tmp_path):
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(logger, operations_completed=2, operations_failed=2,
                        total_records_processed=50, total_execution_time_seconds=5.0)

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any(e.get("operations_failed") == 2 for e in entries)

    def test_includes_total_records_processed(self, tmp_path):
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(logger, operations_completed=1, operations_failed=0,
                        total_records_processed=9876, total_execution_time_seconds=3.0)

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any(e.get("total_records_processed") == 9876 for e in entries)

    def test_includes_total_execution_time(self, tmp_path):
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(logger, operations_completed=1, operations_failed=0,
                        total_records_processed=0, total_execution_time_seconds=77.3)

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any(
            abs(e.get("total_execution_time_seconds", -1) - 77.3) < 0.01
            for e in entries
        )

    def test_summary_message_is_run_complete(self, tmp_path):
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(logger, operations_completed=1, operations_failed=0,
                        total_records_processed=0, total_execution_time_seconds=1.0)

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert any("Run complete" in e.get("message", "") for e in entries)

    def test_all_four_fields_present(self, tmp_path):
        """All four required summary fields must be present in the same entry."""
        log_path = str(tmp_path / "summary.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logger = logging.getLogger("test.summary")
        log_run_summary(
            logger,
            operations_completed=5,
            operations_failed=2,
            total_records_processed=3000,
            total_execution_time_seconds=99.0,
        )

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        required_fields = {
            "operations_completed",
            "operations_failed",
            "total_records_processed",
            "total_execution_time_seconds",
        }
        assert any(required_fields.issubset(e.keys()) for e in entries)


# ---------------------------------------------------------------------------
# JSON format tests
# ---------------------------------------------------------------------------

class TestJsonFormat:
    def test_log_entry_is_valid_json(self, tmp_path):
        log_path = str(tmp_path / "json.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logging.getLogger().info("test message")

        for h in logging.getLogger().handlers:
            h.flush()

        raw_lines = Path(log_path).read_text(encoding="utf-8").strip().splitlines()
        assert len(raw_lines) >= 1
        # Each line must be parseable as JSON
        for line in raw_lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_log_entry_has_level_field(self, tmp_path):
        log_path = str(tmp_path / "json.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logging.getLogger().info("level test")

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert all("level" in e for e in entries)

    def test_log_entry_has_timestamp_field(self, tmp_path):
        log_path = str(tmp_path / "json.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logging.getLogger().info("timestamp test")

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert all("timestamp" in e for e in entries)

    def test_log_entry_has_message_field(self, tmp_path):
        log_path = str(tmp_path / "json.log")
        config = _make_config(log_file=log_path)
        configure_logging(config)

        logging.getLogger().info("message field test")

        for h in logging.getLogger().handlers:
            h.flush()

        entries = _read_log_lines(log_path)
        assert all("message" in e for e in entries)


# ---------------------------------------------------------------------------
# Hypothesis strategy for Property 21
# ---------------------------------------------------------------------------

@st.composite
def run_summary_strategy(draw):
    """Generate a dict of valid run-summary field values.

    Fields generated:
      - operations_completed: non-negative int (0–1000)
      - operations_failed: non-negative int (0–100)
      - total_records_processed: non-negative int (0–1_000_000)
      - total_execution_time_seconds: non-negative float (0.0–3600.0)
    """
    return {
        "operations_completed": draw(st.integers(min_value=0, max_value=1_000)),
        "operations_failed": draw(st.integers(min_value=0, max_value=100)),
        "total_records_processed": draw(st.integers(min_value=0, max_value=1_000_000)),
        "total_execution_time_seconds": draw(
            st.floats(
                min_value=0.0,
                max_value=3600.0,
                allow_nan=False,
                allow_infinity=False,
            )
        ),
    }


def _capture_logger(name: str = "test_run_summary") -> tuple[logging.Logger, list[logging.LogRecord]]:
    """Return a (logger, records) pair backed by an in-memory capturing handler.

    Useful for property tests that don't need the full JSON file pipeline.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    logger.propagate = False

    records: list[logging.LogRecord] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger.addHandler(_CapturingHandler())
    return logger, records


# ---------------------------------------------------------------------------
# Property 21: Run summary completeness (Requirements 9.5)
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 21: Run summary completeness
@given(run_summary_strategy())
@settings(max_examples=100)
def test_run_summary_completeness(summary_data: dict) -> None:
    """Validates: Requirements 9.5

    For any execution run (with any mix of operation successes and failures),
    the final run summary should contain non-null values for:
      - operations_completed
      - operations_failed
      - total_records_processed
      - total_execution_time_seconds
    """
    logger, records = _capture_logger("prop21_" + str(id(summary_data)))

    log_run_summary(
        logger,
        operations_completed=summary_data["operations_completed"],
        operations_failed=summary_data["operations_failed"],
        total_records_processed=summary_data["total_records_processed"],
        total_execution_time_seconds=summary_data["total_execution_time_seconds"],
    )

    # Exactly one log record must be produced
    assert len(records) == 1, (
        f"Expected exactly one log record, got {len(records)}"
    )

    record = records[0]

    # All four required fields must be present and non-null
    assert hasattr(record, "operations_completed"), (
        "Log record is missing 'operations_completed'"
    )
    assert record.operations_completed is not None, (
        "'operations_completed' must not be None"
    )

    assert hasattr(record, "operations_failed"), (
        "Log record is missing 'operations_failed'"
    )
    assert record.operations_failed is not None, (
        "'operations_failed' must not be None"
    )

    assert hasattr(record, "total_records_processed"), (
        "Log record is missing 'total_records_processed'"
    )
    assert record.total_records_processed is not None, (
        "'total_records_processed' must not be None"
    )

    assert hasattr(record, "total_execution_time_seconds"), (
        "Log record is missing 'total_execution_time_seconds'"
    )
    assert record.total_execution_time_seconds is not None, (
        "'total_execution_time_seconds' must not be None"
    )

    # Values must match the inputs exactly
    assert record.operations_completed == summary_data["operations_completed"]
    assert record.operations_failed == summary_data["operations_failed"]
    assert record.total_records_processed == summary_data["total_records_processed"]
    assert record.total_execution_time_seconds == summary_data["total_execution_time_seconds"]


# ---------------------------------------------------------------------------
# Example-based tests for Property 21
# ---------------------------------------------------------------------------

class TestLogRunSummaryProperty21Examples:
    """Example-based tests verifying run summary completeness.

    These complement the property test above with concrete, readable scenarios.
    Validates: Requirements 9.5
    """

    def test_produces_exactly_one_log_record(self) -> None:
        """log_run_summary emits exactly one log record per call."""
        logger, records = _capture_logger("ex21_one_record")
        log_run_summary(logger, 5, 1, 100, 3.14)
        assert len(records) == 1

    def test_log_record_is_at_info_level(self) -> None:
        """The log record emitted is at INFO level."""
        logger, records = _capture_logger("ex21_info_level")
        log_run_summary(logger, 5, 1, 100, 3.14)
        assert records[0].levelno == logging.INFO

    def test_all_four_required_fields_present(self) -> None:
        """The log record contains all four required fields as attributes."""
        logger, records = _capture_logger("ex21_four_fields")
        log_run_summary(logger, 3, 2, 500, 10.5)
        record = records[0]
        assert hasattr(record, "operations_completed")
        assert hasattr(record, "operations_failed")
        assert hasattr(record, "total_records_processed")
        assert hasattr(record, "total_execution_time_seconds")

    def test_all_four_required_fields_are_non_null(self) -> None:
        """None of the four required fields is None."""
        logger, records = _capture_logger("ex21_non_null")
        log_run_summary(logger, 3, 2, 500, 10.5)
        record = records[0]
        assert record.operations_completed is not None
        assert record.operations_failed is not None
        assert record.total_records_processed is not None
        assert record.total_execution_time_seconds is not None

    def test_zero_operations_completed_and_failed_is_valid(self) -> None:
        """Zero operations_completed and zero operations_failed is a valid state."""
        logger, records = _capture_logger("ex21_all_zeros")
        log_run_summary(logger, 0, 0, 0, 0.0)
        assert len(records) == 1
        record = records[0]
        assert record.operations_completed == 0
        assert record.operations_failed == 0
        assert record.total_records_processed == 0
        assert record.total_execution_time_seconds == 0.0

    def test_large_total_records_processed_is_handled(self) -> None:
        """Large numbers (e.g. total_records_processed=1_000_000) are handled correctly."""
        logger, records = _capture_logger("ex21_large_records")
        log_run_summary(logger, 10, 2, 1_000_000, 3600.0)
        record = records[0]
        assert record.total_records_processed == 1_000_000
        assert record.total_execution_time_seconds == 3600.0
