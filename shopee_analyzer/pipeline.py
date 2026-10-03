"""Pipeline orchestrator for the Shopee Sales Analyzer.

Coordinates the full execution sequence:
    authenticate → collect → analyse → recommend → report → log summary

Supports both a full run (``run``) for a specified Sales_Period and an
incremental run (``run_incremental``) that collects data since the last
successful refresh.

Requirements: 2.1, 3.1, 4.1, 5.1, 7.2, 9.1, 9.5
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shopee_analyzer.auth import AuthClient
from shopee_analyzer.collector import DataCollector
from shopee_analyzer.kpi_engine import KPIEngine
from shopee_analyzer.logging_setup import log_operation, log_run_summary
from shopee_analyzer.models import (
    AnalysisResult,
    CollectionResult,
    Configuration,
    SalesPeriod,
)
from shopee_analyzer.recommendation_engine import RecommendationEngine
from shopee_analyzer.report_generator import ReportGenerator
from shopee_analyzer.storage import DataStore

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the full Shopee Sales Analyzer execution sequence.

    Each public method wraps every constituent operation in try/except so
    that a failure in one step is logged and counted, but does not abort
    subsequent steps.  A structured run summary is always emitted at the
    end, even when operations fail.

    Requirements: 7.5, 9.2, 9.5
    """

    def __init__(
        self,
        config: Configuration,
        auth_client: AuthClient,
        data_collector: DataCollector,
        data_store: DataStore,
        kpi_engine: KPIEngine,
        recommendation_engine: RecommendationEngine,
        report_generator: ReportGenerator,
    ) -> None:
        """Initialise the pipeline with all required collaborators.

        Args:
            config: Validated :class:`Configuration` object.
            auth_client: :class:`AuthClient` for token management.
            data_collector: :class:`DataCollector` for order/product collection.
            data_store: :class:`DataStore` for reading persisted data and
                        looking up the last successful run timestamp.
            kpi_engine: :class:`KPIEngine` for computing KPIs and metrics.
            recommendation_engine: :class:`RecommendationEngine` for generating
                                    recommendations.
            report_generator: :class:`ReportGenerator` for writing output files.
        """
        self._config = config
        self._auth_client = auth_client
        self._data_collector = data_collector
        self._data_store = data_store
        self._kpi_engine = kpi_engine
        self._recommendation_engine = recommendation_engine
        self._report_generator = report_generator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, period: SalesPeriod) -> AnalysisResult:
        """Execute the full analysis pipeline for *period*.

        Sequence:
        1.  ``auth_client.get_valid_token()``
        2.  ``data_collector.collect_orders(period)``
        3.  ``data_collector.collect_products()``
        4.  ``data_store.load_orders(period)``
        5.  ``data_store.load_products()``
        6.  ``kpi_engine.compute_store_kpis(orders, period)``
        7.  ``kpi_engine.compute_product_metrics(orders, products)``
        8.  ``recommendation_engine.generate(kpis, product_metrics)``
        9.  Build :class:`AnalysisResult`
        10. ``report_generator.write_json_summary(result, output_dir)``
        11. ``report_generator.write_product_csv(product_metrics, output_dir)``
        12. ``report_generator.write_powerbi_export(result, output_dir)``
        13. Log structured entry per operation and final run summary.
        14. Return :class:`AnalysisResult`

        Each step is wrapped in try/except.  Non-critical failures are
        logged and counted.  If the pipeline cannot produce an
        ``AnalysisResult`` (steps 1–9), the original exception is re-raised
        after the run summary is logged.

        Requirements: 2.1, 3.1, 4.1, 5.1, 9.1, 9.2, 9.5
        """
        run_start = time.time()
        operations_completed = 0
        operations_failed = 0
        total_records_processed = 0

        output_dir = Path(self._config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        _fatal_exc: BaseException | None = None

        # ----------------------------------------------------------
        # Step 1: Authenticate
        # ----------------------------------------------------------
        try:
            self._auth_client.get_valid_token()
            log_operation(
                logger,
                operation="authentication",
                shop_id=self._config.shop_id,
                message="Token validated/refreshed successfully.",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Authentication failed: %s", exc)
            log_operation(
                logger,
                operation="authentication",
                shop_id=self._config.shop_id,
                message=f"Authentication failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 2: Collect orders
        # ----------------------------------------------------------
        orders_result: CollectionResult | None = None
        try:
            orders_result = self._data_collector.collect_orders(period)
            total_records_processed += orders_result.records_collected
            log_operation(
                logger,
                operation="data_collection",
                shop_id=self._config.shop_id,
                message="Order collection complete.",
                records_collected=orders_result.records_collected,
                records_failed=orders_result.records_failed,
                duration_seconds=orders_result.duration_seconds,
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Order collection failed: %s", exc)
            log_operation(
                logger,
                operation="data_collection",
                shop_id=self._config.shop_id,
                message=f"Order collection failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 3: Collect products
        # ----------------------------------------------------------
        products_result: CollectionResult | None = None
        try:
            products_result = self._data_collector.collect_products()
            total_records_processed += products_result.records_collected
            log_operation(
                logger,
                operation="product_collection",
                shop_id=self._config.shop_id,
                message="Product collection complete.",
                records_collected=products_result.records_collected,
                records_failed=products_result.records_failed,
                duration_seconds=products_result.duration_seconds,
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Product collection failed: %s", exc)
            log_operation(
                logger,
                operation="product_collection",
                shop_id=self._config.shop_id,
                message=f"Product collection failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 4: Load orders from data store
        # ----------------------------------------------------------
        orders = []
        try:
            orders = self._data_store.load_orders(period)
            log_operation(
                logger,
                operation="load_orders",
                shop_id=self._config.shop_id,
                message=f"Loaded {len(orders)} order(s) from data store.",
                records_loaded=len(orders),
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Failed to load orders from data store: %s", exc)
            log_operation(
                logger,
                operation="load_orders",
                shop_id=self._config.shop_id,
                message=f"Failed to load orders: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 5: Load products from data store
        # ----------------------------------------------------------
        products = []
        try:
            products = self._data_store.load_products()
            log_operation(
                logger,
                operation="load_products",
                shop_id=self._config.shop_id,
                message=f"Loaded {len(products)} product(s) from data store.",
                records_loaded=len(products),
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Failed to load products from data store: %s", exc)
            log_operation(
                logger,
                operation="load_products",
                shop_id=self._config.shop_id,
                message=f"Failed to load products: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 6: Compute store KPIs
        # ----------------------------------------------------------
        kpis = None
        try:
            kpis = self._kpi_engine.compute_store_kpis(orders, period)
            log_operation(
                logger,
                operation="kpi_computation",
                shop_id=self._config.shop_id,
                message="Store KPIs computed.",
                total_revenue=str(kpis.total_revenue),
                total_orders=kpis.total_orders,
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("KPI computation failed: %s", exc)
            log_operation(
                logger,
                operation="kpi_computation",
                shop_id=self._config.shop_id,
                message=f"KPI computation failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 7: Compute per-product metrics
        # ----------------------------------------------------------
        product_metrics = []
        try:
            product_metrics = self._kpi_engine.compute_product_metrics(orders, products)
            log_operation(
                logger,
                operation="product_metrics_computation",
                shop_id=self._config.shop_id,
                message=f"Computed metrics for {len(product_metrics)} product(s).",
                products_analysed=len(product_metrics),
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Product metrics computation failed: %s", exc)
            log_operation(
                logger,
                operation="product_metrics_computation",
                shop_id=self._config.shop_id,
                message=f"Product metrics computation failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 8: Generate recommendations
        # ----------------------------------------------------------
        recommendations = []
        try:
            if kpis is not None:
                recommendations = self._recommendation_engine.generate(kpis, product_metrics)
            log_operation(
                logger,
                operation="recommendation_generation",
                shop_id=self._config.shop_id,
                message=f"Generated {len(recommendations)} recommendation(s).",
                recommendations_count=len(recommendations),
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Recommendation generation failed: %s", exc)
            log_operation(
                logger,
                operation="recommendation_generation",
                shop_id=self._config.shop_id,
                message=f"Recommendation generation failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Step 9: Build AnalysisResult
        # If we cannot build a result (e.g. kpis is None), we defer
        # raising until after the summary is logged.
        # ----------------------------------------------------------
        result: AnalysisResult | None = None
        try:
            if kpis is None:
                raise ValueError(
                    "KPIs could not be computed; cannot build AnalysisResult."
                )
            result = AnalysisResult(
                shop_id=self._config.shop_id,
                period=period,
                kpis=kpis,
                product_metrics=product_metrics,
                recommendations=recommendations,
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Failed to build AnalysisResult: %s", exc)
            operations_failed += 1
            if _fatal_exc is None:
                _fatal_exc = exc

        # ----------------------------------------------------------
        # Steps 10–12: Reporting (non-critical; failures are logged)
        # ----------------------------------------------------------
        if result is not None:
            # Step 10: Write JSON summary
            try:
                self._report_generator.write_json_summary(result, output_dir)
                log_operation(
                    logger,
                    operation="report_json_summary",
                    shop_id=self._config.shop_id,
                    message="JSON summary written.",
                    outcome="success",
                )
                operations_completed += 1
            except Exception as exc:
                logger.exception("Failed to write JSON summary: %s", exc)
                log_operation(
                    logger,
                    operation="report_json_summary",
                    shop_id=self._config.shop_id,
                    message=f"Failed to write JSON summary: {exc}",
                    outcome="failure",
                )
                operations_failed += 1

            # Step 11: Write product CSV
            try:
                self._report_generator.write_product_csv(product_metrics, output_dir)
                log_operation(
                    logger,
                    operation="report_product_csv",
                    shop_id=self._config.shop_id,
                    message="Product CSV written.",
                    outcome="success",
                )
                operations_completed += 1
            except Exception as exc:
                logger.exception("Failed to write product CSV: %s", exc)
                log_operation(
                    logger,
                    operation="report_product_csv",
                    shop_id=self._config.shop_id,
                    message=f"Failed to write product CSV: {exc}",
                    outcome="failure",
                )
                operations_failed += 1

            # Step 12: Write Power BI export
            try:
                self._report_generator.write_powerbi_export(result, output_dir)
                log_operation(
                    logger,
                    operation="report_powerbi_export",
                    shop_id=self._config.shop_id,
                    message="Power BI export written.",
                    outcome="success",
                )
                operations_completed += 1
            except Exception as exc:
                logger.exception("Failed to write Power BI export: %s", exc)
                log_operation(
                    logger,
                    operation="report_powerbi_export",
                    shop_id=self._config.shop_id,
                    message=f"Failed to write Power BI export: {exc}",
                    outcome="failure",
                )
                operations_failed += 1

        # ----------------------------------------------------------
        # Always log run summary (Requirement 9.5)
        # ----------------------------------------------------------
        total_execution_time = time.time() - run_start
        log_run_summary(
            logger,
            operations_completed=operations_completed,
            operations_failed=operations_failed,
            total_records_processed=total_records_processed,
            total_execution_time_seconds=total_execution_time,
        )

        # If we could not produce a result, re-raise the first fatal exception.
        if result is None:
            raise _fatal_exc  # type: ignore[misc]

        return result

    def run_incremental(self, since: datetime | None = None) -> CollectionResult:
        """Run an incremental data collection starting from *since*.

        If *since* is ``None``, the timestamp is determined as follows:
        1. Query ``data_store.get_last_successful_run()``.
        2. If still ``None``, fall back to
           ``now() - timedelta(days=config.default_period_days)``.

        The method collects orders and products only (no analysis or
        reporting) and returns a merged :class:`CollectionResult`.

        Requirements: 7.2
        """
        run_start = time.time()
        operations_completed = 0
        operations_failed = 0
        total_records_processed = 0

        now = datetime.now(timezone.utc)

        # ----------------------------------------------------------
        # Determine the 'since' timestamp
        # ----------------------------------------------------------
        if since is None:
            since = self._data_store.get_last_successful_run()
        if since is None:
            since = now - timedelta(days=self._config.default_period_days)

        period = SalesPeriod(start=since, end=now)

        log_operation(
            logger,
            operation="incremental_collection",
            shop_id=self._config.shop_id,
            message=f"Starting incremental collection from {since.isoformat()}.",
            since=since.isoformat(),
            outcome="started",
        )

        # ----------------------------------------------------------
        # Collect orders
        # ----------------------------------------------------------
        orders_result = CollectionResult(
            records_collected=0, records_failed=0, failed_ids=[], duration_seconds=0.0
        )
        try:
            orders_result = self._data_collector.collect_orders(period)
            total_records_processed += orders_result.records_collected
            log_operation(
                logger,
                operation="incremental_order_collection",
                shop_id=self._config.shop_id,
                message="Incremental order collection complete.",
                records_collected=orders_result.records_collected,
                records_failed=orders_result.records_failed,
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Incremental order collection failed: %s", exc)
            log_operation(
                logger,
                operation="incremental_order_collection",
                shop_id=self._config.shop_id,
                message=f"Incremental order collection failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1

        # ----------------------------------------------------------
        # Collect products
        # ----------------------------------------------------------
        products_result = CollectionResult(
            records_collected=0, records_failed=0, failed_ids=[], duration_seconds=0.0
        )
        try:
            products_result = self._data_collector.collect_products()
            total_records_processed += products_result.records_collected
            log_operation(
                logger,
                operation="incremental_product_collection",
                shop_id=self._config.shop_id,
                message="Incremental product collection complete.",
                records_collected=products_result.records_collected,
                records_failed=products_result.records_failed,
                outcome="success",
            )
            operations_completed += 1
        except Exception as exc:
            logger.exception("Incremental product collection failed: %s", exc)
            log_operation(
                logger,
                operation="incremental_product_collection",
                shop_id=self._config.shop_id,
                message=f"Incremental product collection failed: {exc}",
                outcome="failure",
            )
            operations_failed += 1

        # ----------------------------------------------------------
        # Merge results
        # ----------------------------------------------------------
        merged = CollectionResult(
            records_collected=orders_result.records_collected + products_result.records_collected,
            records_failed=orders_result.records_failed + products_result.records_failed,
            failed_ids=orders_result.failed_ids + products_result.failed_ids,
            duration_seconds=max(orders_result.duration_seconds, products_result.duration_seconds),
        )

        total_execution_time = time.time() - run_start
        log_run_summary(
            logger,
            operations_completed=operations_completed,
            operations_failed=operations_failed,
            total_records_processed=total_records_processed,
            total_execution_time_seconds=total_execution_time,
        )

        return merged
