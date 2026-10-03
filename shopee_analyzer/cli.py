"""CLI entry point for the Shopee Sales Analyzer.

Provides four sub-commands:
  run              — full pipeline run for a date range
  run-incremental  — incremental collection since the last successful run
  status           — show last successful run timestamp and history count
  history          — print last N refresh history entries

Exit codes:
  0 — success
  1 — ConfigurationError (bad or missing config)
  2 — any other fatal exception

Requirements: 8.1, 8.2, 8.3, 7.4, 10.1, 10.2
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from shopee_analyzer.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# Component wiring helper
# ---------------------------------------------------------------------------

def _build_pipeline(args: argparse.Namespace):
    """Parse config, wire all components, and return (config, pipeline).

    Raises ConfigurationError on bad config; ImportError if pipeline module
    is not yet available (task 15.1 not yet implemented).
    """
    # Import pipeline here so that ImportError is surfaced clearly
    try:
        from shopee_analyzer.pipeline import Pipeline  # noqa: F401 — explicit import
    except ImportError as exc:
        raise ImportError(
            "Pipeline module is not available yet (shopee_analyzer/pipeline.py "
            "has not been implemented). Run task 15.1 first."
        ) from exc

    from shopee_analyzer.auth import AuthClient
    from shopee_analyzer.collector import DataCollector
    from shopee_analyzer.config import ConfigurationParser
    from shopee_analyzer.kpi_engine import KPIEngine
    from shopee_analyzer.logging_setup import configure_logging
    from shopee_analyzer.pipeline import Pipeline  # type: ignore[import]
    from shopee_analyzer.recommendation_engine import RecommendationEngine
    from shopee_analyzer.report_generator import ReportGenerator
    from shopee_analyzer.storage import DataStore

    config = ConfigurationParser().parse(Path(args.config))
    configure_logging(config)

    data_store = DataStore(config.data_store_path)
    auth_client = AuthClient(config, data_store)
    collector = DataCollector(config, auth_client, data_store)
    kpi_engine = KPIEngine()
    rec_engine = RecommendationEngine()
    report_gen = ReportGenerator()

    pipeline = Pipeline(
        config,
        auth_client,
        collector,
        data_store,
        kpi_engine,
        rec_engine,
        report_gen,
    )
    return config, pipeline, data_store


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------

def _cmd_run(args: argparse.Namespace) -> None:
    """Execute a full pipeline run for the requested period.

    Prints a summary of total_revenue, total_orders, total_units_sold, and
    the number of recommendations generated.
    """
    from shopee_analyzer.models import SalesPeriod

    period_days: int = args.period_days
    now = datetime.now()
    period = SalesPeriod(
        start=now - timedelta(days=period_days),
        end=now,
    )

    _config, pipeline, _ds = _build_pipeline(args)

    result = pipeline.run(period)

    # Print summary
    kpis = result.kpis
    recs = result.recommendations
    print("=== Shopee Analyzer Run Summary ===")
    print(f"Period       : {period.start.strftime('%Y-%m-%d')} → {period.end.strftime('%Y-%m-%d')}")
    print(f"Total revenue: {kpis.total_revenue}")
    print(f"Total orders : {kpis.total_orders}")
    print(f"Units sold   : {kpis.total_units_sold}")
    print(f"Recommendations: {len(recs)}")


def _cmd_run_incremental(args: argparse.Namespace) -> None:
    """Execute an incremental collection run since the last successful run.

    Prints the number of records collected.
    """
    _config, pipeline, _ds = _build_pipeline(args)

    result = pipeline.run_incremental()

    print("=== Incremental Run Summary ===")
    print(f"Records collected: {result.records_collected}")
    print(f"Records failed   : {result.records_failed}")
    print(f"Duration         : {result.duration_seconds:.2f}s")


def _cmd_status(args: argparse.Namespace) -> None:
    """Print the last successful run timestamp and total history count."""
    from shopee_analyzer.config import ConfigurationParser
    from shopee_analyzer.logging_setup import configure_logging
    from shopee_analyzer.storage import DataStore

    config = ConfigurationParser().parse(Path(args.config))
    configure_logging(config)
    data_store = DataStore(config.data_store_path)

    last_run = data_store.get_last_successful_run()
    history = data_store.load_refresh_history()

    print("=== Shopee Analyzer Status ===")
    if last_run is not None:
        print(f"Last successful run : {last_run.isoformat()}")
    else:
        print("Last successful run : No runs yet")
    print(f"Total history count: {len(history)}")


def _cmd_history(args: argparse.Namespace) -> None:
    """Print the last N refresh history entries (default 10)."""
    from shopee_analyzer.config import ConfigurationParser
    from shopee_analyzer.logging_setup import configure_logging
    from shopee_analyzer.storage import DataStore

    config = ConfigurationParser().parse(Path(args.config))
    configure_logging(config)
    data_store = DataStore(config.data_store_path)

    n: int = args.last
    history = data_store.load_refresh_history()
    recent = history[-n:] if history else []

    print("=== Refresh History (last {:d}) ===".format(n))

    if not recent:
        print("No history entries found.")
        return

    # Header
    header = (
        f"{'run_id':<36}  "
        f"{'run_timestamp':<26}  "
        f"{'status':<8}  "
        f"{'records':>8}  "
        f"{'duration_s':>10}  "
        f"failure_reason"
    )
    print(header)
    print("-" * len(header))

    for entry in recent:
        ts_str = entry.run_timestamp.isoformat() if entry.run_timestamp else "N/A"
        reason = entry.failure_reason or ""
        print(
            f"{entry.run_id:<36}  "
            f"{ts_str:<26}  "
            f"{entry.status:<8}  "
            f"{entry.records_collected:>8}  "
            f"{entry.duration_seconds:>10.2f}  "
            f"{reason}"
        )


# ---------------------------------------------------------------------------
# Argument parser construction
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser with all sub-commands."""
    parser = argparse.ArgumentParser(
        prog="shopee-analyzer",
        description=(
            "Shopee Sales Analyzer — collect sales data, compute KPIs, "
            "generate recommendations, and export Power BI reports."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    run_p = subparsers.add_parser(
        "run",
        help="Run the full analysis pipeline for a date range.",
    )
    run_p.add_argument(
        "--config",
        required=True,
        metavar="CONFIG",
        help="Path to the YAML or JSON configuration file.",
    )
    run_p.add_argument(
        "--period-days",
        type=int,
        default=30,
        metavar="DAYS",
        dest="period_days",
        help="Number of days to look back (default: 30).",
    )
    run_p.set_defaults(func=_cmd_run)

    # --- run-incremental ---
    inc_p = subparsers.add_parser(
        "run-incremental",
        help="Run an incremental collection since the last successful run.",
    )
    inc_p.add_argument(
        "--config",
        required=True,
        metavar="CONFIG",
        help="Path to the YAML or JSON configuration file.",
    )
    inc_p.set_defaults(func=_cmd_run_incremental)

    # --- status ---
    status_p = subparsers.add_parser(
        "status",
        help="Show the last successful run timestamp and total history count.",
    )
    status_p.add_argument(
        "--config",
        required=True,
        metavar="CONFIG",
        help="Path to the YAML or JSON configuration file.",
    )
    status_p.set_defaults(func=_cmd_status)

    # --- history ---
    hist_p = subparsers.add_parser(
        "history",
        help="Print the last N refresh history entries.",
    )
    hist_p.add_argument(
        "--config",
        required=True,
        metavar="CONFIG",
        help="Path to the YAML or JSON configuration file.",
    )
    hist_p.add_argument(
        "--last",
        type=int,
        default=10,
        metavar="N",
        help="Number of most-recent history entries to display (default: 10).",
    )
    hist_p.set_defaults(func=_cmd_history)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main CLI entry point.

    Dispatches to the appropriate sub-command handler.  Catches
    :class:`~shopee_analyzer.exceptions.ConfigurationError` and exits with
    code 1; any other fatal exception exits with code 2.

    Requirements: 8.1, 8.2, 8.3, 7.4
    """
    parser = _build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
