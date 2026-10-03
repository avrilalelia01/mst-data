"""Scheduled data refresh for the Shopee Sales Analyzer.

Wraps APScheduler's BackgroundScheduler to run incremental data collection
on a configured hourly / daily / weekly cadence and records every run in the
Data Store's refresh_history table.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, Union

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from shopee_analyzer.exceptions import SchedulerError
from shopee_analyzer.models import CollectionResult, Configuration, RefreshHistoryEntry

if TYPE_CHECKING:
    from shopee_analyzer.storage import DataStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline protocol — avoids a hard import cycle
# ---------------------------------------------------------------------------

class Pipeline(Protocol):
    """Minimal interface that RefreshScheduler depends on."""

    def run_incremental(self, since: datetime | None = None) -> CollectionResult:
        """Run an incremental data collection starting from *since*.

        Returns a :class:`CollectionResult` with the number of records collected.
        """
        ...


# ---------------------------------------------------------------------------
# Schedule → APScheduler trigger mapping
# ---------------------------------------------------------------------------

def _build_trigger(
    refresh_schedule: str,
) -> Union[IntervalTrigger, CronTrigger]:
    """Map a schedule string to an APScheduler trigger.

    Mapping:
      - ``"hourly"``  → :class:`IntervalTrigger` (hours=1)
      - ``"daily"``   → :class:`CronTrigger` (hour=0, minute=0)
      - ``"weekly"``  → :class:`CronTrigger` (day_of_week="mon", hour=0, minute=0)

    Parameters
    ----------
    refresh_schedule:
        One of ``"hourly"``, ``"daily"``, or ``"weekly"``.

    Returns
    -------
    IntervalTrigger | CronTrigger

    Raises
    ------
    SchedulerError
        For any value not in the recognised set.
    """
    if refresh_schedule == "hourly":
        return IntervalTrigger(hours=1)
    elif refresh_schedule == "daily":
        return CronTrigger(hour=0, minute=0)
    elif refresh_schedule == "weekly":
        return CronTrigger(day_of_week="mon", hour=0, minute=0)
    else:
        raise SchedulerError(
            f"Unknown refresh_schedule value: '{refresh_schedule}'. "
            "Expected one of: hourly, daily, weekly."
        )


# ---------------------------------------------------------------------------
# RefreshScheduler
# ---------------------------------------------------------------------------

class RefreshScheduler:
    """Wraps APScheduler BackgroundScheduler with business-logic refresh logic.

    Requirements: 7.1, 7.2, 7.3, 7.4, 7.5

    Usage::

        scheduler = RefreshScheduler(data_store)
        scheduler.start(config, pipeline)
        # ... runs in background ...
        scheduler.stop()
        history = scheduler.get_history()
    """

    def __init__(self, data_store: DataStore) -> None:
        self._data_store = data_store
        self._scheduler: BackgroundScheduler | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, config: Configuration, pipeline: Pipeline) -> None:
        """Build the trigger from *config* and start the background scheduler.

        Parameters
        ----------
        config:
            Validated application configuration.  ``config.refresh_schedule``
            must be one of ``"hourly"``, ``"daily"``, ``"weekly"``.
        pipeline:
            The pipeline object whose ``run_incremental`` method is called on
            each scheduled tick.

        Raises
        ------
        SchedulerError
            If ``config.refresh_schedule`` is not a recognised value.
        """
        trigger = _build_trigger(config.refresh_schedule)

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            func=self._run_refresh,
            trigger=trigger,
            args=[pipeline],
            id="refresh_job",
            name="Shopee data refresh",
        )
        self._scheduler.start()
        logger.info(
            "RefreshScheduler started with schedule=%s", config.refresh_schedule
        )

    def stop(self) -> None:
        """Shut down the background scheduler if it is running.

        Uses ``wait=False`` so the call returns immediately without blocking
        until the current job completes.  Safe to call even when the scheduler
        has not been started or has already been stopped.
        """
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("RefreshScheduler stopped.")
        self._scheduler = None

    def get_history(self) -> list[RefreshHistoryEntry]:
        """Return all refresh history entries from the Data Store.

        Requirements: 7.4
        """
        return self._data_store.load_refresh_history()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_refresh(self, pipeline: Pipeline) -> None:
        """Execute one incremental refresh and persist the result.

        Runs inside the APScheduler background thread.  Any exception is
        caught, logged, and recorded as a FAILURE entry — the scheduler will
        retry at the next scheduled interval per Requirement 7.3.

        Requirements: 7.2, 7.3, 7.4, 7.5
        """
        start_time = datetime.now(tz=timezone.utc)
        run_id = str(uuid.uuid4())
        records_collected: int = 0
        status: str = "SUCCESS"
        failure_reason: str | None = None

        try:
            since = self._data_store.get_last_successful_run()
            result: CollectionResult = pipeline.run_incremental(since=since)
            records_collected = result.records_collected
        except Exception as exc:  # noqa: BLE001
            status = "FAILURE"
            failure_reason = str(exc)
            logger.error(
                "Scheduled refresh failed run_id=%s: %s",
                run_id,
                exc,
                exc_info=True,
            )

        duration = (datetime.now(tz=timezone.utc) - start_time).total_seconds()
        entry = RefreshHistoryEntry(
            run_id=run_id,
            run_timestamp=start_time,
            status=status,  # type: ignore[arg-type]
            records_collected=records_collected,
            duration_seconds=duration,
            failure_reason=failure_reason,
        )
        try:
            self._data_store.save_refresh_history(entry)
        except Exception as save_exc:  # noqa: BLE001
            logger.error(
                "Failed to persist refresh history for run_id=%s: %s",
                run_id,
                save_exc,
            )

        if status == "SUCCESS":
            logger.info(
                "Scheduled refresh succeeded run_id=%s records=%d duration=%.2fs",
                run_id,
                records_collected,
                duration,
            )
