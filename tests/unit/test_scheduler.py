"""Unit and property-based tests for RefreshScheduler.

Covers:
  - Property 18: Incremental collection window correctness (Requirements 7.2)
  - Property 19: Refresh history entry completeness (Requirements 7.4)
  - Example-based tests for successful and failed refresh runs
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shopee_analyzer.models import CollectionResult, RefreshHistoryEntry
from shopee_analyzer.scheduler import RefreshScheduler
from shopee_analyzer.storage import DataStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_in_memory_store() -> DataStore:
    """Return an in-memory SQLite DataStore suitable for tests."""
    return DataStore(":memory:")


def make_scheduler() -> tuple[RefreshScheduler, DataStore]:
    """Return a (RefreshScheduler, DataStore) pair backed by in-memory SQLite."""
    store = make_in_memory_store()
    scheduler = RefreshScheduler(data_store=store)
    return scheduler, store


def make_success_pipeline(records_collected: int = 10) -> MagicMock:
    """Return a mock Pipeline that succeeds with *records_collected* records."""
    pipeline = MagicMock()
    pipeline.run_incremental.return_value = CollectionResult(
        records_collected=records_collected,
        records_failed=0,
        failed_ids=[],
        duration_seconds=1.0,
    )
    return pipeline


def make_failure_pipeline(error_message: str = "API error") -> MagicMock:
    """Return a mock Pipeline whose run_incremental raises RuntimeError."""
    pipeline = MagicMock()
    pipeline.run_incremental.side_effect = RuntimeError(error_message)
    return pipeline


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

@st.composite
def collection_result_strategy(draw) -> CollectionResult:
    """Generate a valid CollectionResult (successful run outcome)."""
    records_collected = draw(st.integers(min_value=0, max_value=10_000))
    records_failed = draw(st.integers(min_value=0, max_value=100))
    duration_seconds = draw(
        st.floats(
            min_value=0.001,
            max_value=3600.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    failed_ids = [str(i) for i in range(records_failed)]
    return CollectionResult(
        records_collected=records_collected,
        records_failed=records_failed,
        failed_ids=failed_ids,
        duration_seconds=duration_seconds,
    )


@st.composite
def failure_message_strategy(draw) -> str:
    """Generate a non-empty failure message string."""
    return draw(st.text(min_size=1, max_size=200))


@st.composite
def run_outcome_strategy(draw):
    """Generate either a successful CollectionResult or a failure message string.

    Represents any possible outcome of a refresh pipeline run.
    """
    is_success = draw(st.booleans())
    if is_success:
        return draw(collection_result_strategy())
    else:
        return draw(failure_message_strategy())


# ---------------------------------------------------------------------------
# Property 18: Incremental collection window correctness (Requirements 7.2)
# ---------------------------------------------------------------------------

@st.composite
def last_successful_run_strategy(draw) -> datetime:
    """Generate a plausible last-run datetime (UTC)."""
    days_ago = draw(st.integers(min_value=0, max_value=365))
    hours_ago = draw(st.integers(min_value=0, max_value=23))
    minutes_ago = draw(st.integers(min_value=0, max_value=59))
    base = datetime.now(tz=timezone.utc) - timedelta(
        days=days_ago, hours=hours_ago, minutes=minutes_ago
    )
    # Truncate to microsecond precision for clean round-trip through SQLite isoformat
    return base.replace(microsecond=0)


# Feature: shopee-sales-analyzer, Property 18: Incremental collection window correctness
@given(last_successful_run_strategy())
@settings(max_examples=100)
def test_incremental_collection_window_correctness(last_run_ts: datetime):
    """Validates: Requirements 7.2

    For any last-successful-run timestamp T stored in the history log,
    the DataStore.get_last_successful_run() should return exactly T so that
    the incremental collection window starts at T.
    """
    store = make_in_memory_store()

    # Seed a SUCCESS entry at the given timestamp
    entry = RefreshHistoryEntry(
        run_id=str(uuid.uuid4()),
        run_timestamp=last_run_ts,
        status="SUCCESS",
        records_collected=10,
        duration_seconds=1.0,
        failure_reason=None,
    )
    store.save_refresh_history(entry)

    reported_last = store.get_last_successful_run()
    assert reported_last is not None

    # Normalise both sides to naive UTC for comparison (SQLite strips tzinfo)
    reported_naive = reported_last.replace(tzinfo=None)
    expected_naive = last_run_ts.replace(tzinfo=None)

    delta = abs((reported_naive - expected_naive).total_seconds())
    assert delta < 1.0, (
        f"Expected last-run at {expected_naive}, got {reported_naive} "
        f"(delta={delta:.3f}s)"
    )


# ---------------------------------------------------------------------------
# Property 19: Refresh history entry completeness (Requirements 7.4)
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 19: Refresh history entry completeness
@given(run_outcome_strategy())
@settings(max_examples=100)
def test_refresh_history_entry_completeness(outcome):
    """Validates: Requirements 7.4

    For any refresh run (successful or failed), the resulting
    RefreshHistoryEntry must have non-null values for:
      - run_id            (non-empty string, valid UUID)
      - run_timestamp     (datetime object)
      - status            ("SUCCESS" or "FAILURE")
      - records_collected (non-negative int)
      - duration_seconds  (non-negative float)
    """
    scheduler, store = make_scheduler()

    if isinstance(outcome, CollectionResult):
        # Successful pipeline: returns a CollectionResult
        pipeline = MagicMock()
        pipeline.run_incremental.return_value = outcome
    else:
        # Failing pipeline: raises RuntimeError with the outcome as message
        error_message = outcome
        pipeline = MagicMock()
        pipeline.run_incremental.side_effect = RuntimeError(error_message)

    # Call the internal method directly (bypasses APScheduler timing)
    scheduler._run_refresh(pipeline)

    history = store.load_refresh_history()
    assert len(history) == 1, "Expected exactly one history entry after one run."

    entry = history[0]

    # --- run_id must be a non-empty string in valid UUID format ---
    assert entry.run_id is not None
    assert isinstance(entry.run_id, str)
    assert len(entry.run_id) > 0
    parsed_id = uuid.UUID(entry.run_id)  # raises ValueError if not valid UUID
    assert str(parsed_id) == entry.run_id

    # --- run_timestamp must be a datetime ---
    assert entry.run_timestamp is not None
    assert isinstance(entry.run_timestamp, datetime)

    # --- status must be "SUCCESS" or "FAILURE" ---
    assert entry.status is not None
    assert entry.status in ("SUCCESS", "FAILURE")

    # --- records_collected must be a non-negative int ---
    assert entry.records_collected is not None
    assert isinstance(entry.records_collected, int)
    assert entry.records_collected >= 0

    # --- duration_seconds must be a non-negative float ---
    assert entry.duration_seconds is not None
    assert isinstance(entry.duration_seconds, float)
    assert entry.duration_seconds >= 0.0

    # --- Cross-check outcome-specific invariants ---
    if isinstance(outcome, CollectionResult):
        assert entry.status == "SUCCESS"
        assert entry.records_collected == outcome.records_collected
        assert entry.failure_reason is None
    else:
        assert entry.status == "FAILURE"
        assert entry.records_collected == 0


# ---------------------------------------------------------------------------
# Example-based tests: successful run
# ---------------------------------------------------------------------------

class TestSuccessfulRun:
    def test_status_is_success(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_success_pipeline(records_collected=42))
        history = store.load_refresh_history()
        assert len(history) == 1
        assert history[0].status == "SUCCESS"

    def test_records_collected_matches_pipeline_result(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_success_pipeline(records_collected=100))
        history = store.load_refresh_history()
        assert history[0].records_collected == 100

    def test_failure_reason_is_none_on_success(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_success_pipeline())
        history = store.load_refresh_history()
        assert history[0].failure_reason is None

    def test_run_id_is_valid_uuid(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_success_pipeline())
        history = store.load_refresh_history()
        run_id = history[0].run_id
        parsed = uuid.UUID(run_id)
        assert str(parsed) == run_id

    def test_run_timestamp_is_close_to_now(self):
        scheduler, store = make_scheduler()
        before = datetime.now(tz=timezone.utc)
        scheduler._run_refresh(make_success_pipeline())
        after = datetime.now(tz=timezone.utc)

        history = store.load_refresh_history()
        ts = history[0].run_timestamp
        # Normalise to UTC (storage may strip tzinfo via isoformat round-trip)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        before_naive = before.replace(tzinfo=None)
        after_naive = (after + timedelta(seconds=5)).replace(tzinfo=None)
        ts_naive = ts.replace(tzinfo=None)

        assert before_naive <= ts_naive <= after_naive

    def test_records_collected_zero_is_valid_success(self):
        """A run that collected zero records is still a SUCCESS."""
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_success_pipeline(records_collected=0))
        history = store.load_refresh_history()
        assert history[0].status == "SUCCESS"
        assert history[0].records_collected == 0

    def test_duration_seconds_is_non_negative(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_success_pipeline())
        history = store.load_refresh_history()
        assert history[0].duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Example-based tests: failed run
# ---------------------------------------------------------------------------

class TestFailedRun:
    def test_status_is_failure_on_exception(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_failure_pipeline("API timeout"))
        history = store.load_refresh_history()
        assert history[0].status == "FAILURE"

    def test_failure_reason_is_non_empty(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_failure_pipeline("Connection refused"))
        history = store.load_refresh_history()
        assert history[0].failure_reason is not None
        assert len(history[0].failure_reason) > 0

    def test_records_collected_is_zero_on_failure(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_failure_pipeline("Bad response"))
        history = store.load_refresh_history()
        assert history[0].records_collected == 0

    def test_run_id_is_valid_uuid_on_failure(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_failure_pipeline())
        history = store.load_refresh_history()
        run_id = history[0].run_id
        parsed = uuid.UUID(run_id)
        assert str(parsed) == run_id

    def test_run_timestamp_present_on_failure(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_failure_pipeline())
        history = store.load_refresh_history()
        assert isinstance(history[0].run_timestamp, datetime)

    def test_failure_reason_contains_exception_message(self):
        scheduler, store = make_scheduler()
        error_msg = "Shopee API rate limit exceeded"
        scheduler._run_refresh(make_failure_pipeline(error_msg))
        history = store.load_refresh_history()
        assert error_msg in history[0].failure_reason

    def test_duration_seconds_is_non_negative_on_failure(self):
        scheduler, store = make_scheduler()
        scheduler._run_refresh(make_failure_pipeline("Fast failure"))
        history = store.load_refresh_history()
        assert history[0].duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Example-based tests: get_history
# ---------------------------------------------------------------------------

class TestGetHistory:
    def test_get_history_returns_all_entries(self):
        scheduler, store = make_scheduler()

        scheduler._run_refresh(make_success_pipeline())
        scheduler._run_refresh(make_failure_pipeline())
        scheduler._run_refresh(make_success_pipeline())

        history = scheduler.get_history()
        assert len(history) == 3

    def test_get_history_entries_ordered_by_timestamp(self):
        scheduler, store = make_scheduler()

        scheduler._run_refresh(make_success_pipeline())
        scheduler._run_refresh(make_failure_pipeline())

        history = scheduler.get_history()
        # Timestamps should be non-decreasing (DataStore orders ASC)
        for i in range(len(history) - 1):
            ts_a = history[i].run_timestamp
            ts_b = history[i + 1].run_timestamp
            ts_a_naive = ts_a.replace(tzinfo=None) if ts_a.tzinfo else ts_a
            ts_b_naive = ts_b.replace(tzinfo=None) if ts_b.tzinfo else ts_b
            assert ts_a_naive <= ts_b_naive

    def test_get_history_empty_before_any_run(self):
        scheduler, store = make_scheduler()
        assert scheduler.get_history() == []
