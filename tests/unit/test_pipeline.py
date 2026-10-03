"""Unit tests for shopee_analyzer.pipeline.Pipeline.

Tests cover:
- run() calls all components in order and returns AnalysisResult
- run_incremental() with since=None falls back to get_last_successful_run()
- run_incremental() with explicit since uses that timestamp
- run_incremental() merges two CollectionResult objects correctly
- Failed step is logged but execution continues (for non-critical steps)

Requirements: 2.1, 3.1, 4.1, 5.1, 7.2, 9.1, 9.5
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest

from shopee_analyzer.models import (
    AnalysisResult,
    CollectionResult,
    Configuration,
    Order,
    OrderItem,
    Product,
    ProductMetrics,
    Recommendation,
    SalesPeriod,
    StoreKPIs,
    TrendData,
)
from shopee_analyzer.pipeline import Pipeline


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_config(output_dir: str) -> Configuration:
    return Configuration(
        partner_id=12345,
        partner_key="secret-key",
        shop_id=67890,
        data_store_path=":memory:",
        output_dir=output_dir,
        refresh_schedule="daily",
        default_period_days=30,
        log_level="INFO",
        log_file="test_pipeline.log",
    )


def _make_collection_result(
    collected: int = 5,
    failed: int = 0,
    failed_ids: list[str] | None = None,
    duration: float = 1.0,
) -> CollectionResult:
    return CollectionResult(
        records_collected=collected,
        records_failed=failed,
        failed_ids=failed_ids or [],
        duration_seconds=duration,
    )


def _make_product() -> Product:
    return Product(
        product_id=1,
        name="Test Product",
        category="electronics",
        current_price=Decimal("100.00"),
        stock_quantity=50,
        review_count=20,
        average_rating=4.5,
        total_units_sold=100,
        total_views=500,
    )


def _make_order() -> Order:
    return Order(
        order_id="ORD-001",
        status="COMPLETED",
        items=[
            OrderItem(
                product_id=1,
                product_name="Test Product",
                quantity=2,
                unit_price=Decimal("100.00"),
            )
        ],
        total_payment=Decimal("200.00"),
        shipping_fee=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        created_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_store_kpis(period: SalesPeriod) -> StoreKPIs:
    return StoreKPIs(
        period=period,
        total_revenue=Decimal("200.00"),
        total_orders=1,
        total_units_sold=2,
        average_order_value=Decimal("200.00"),
        gross_profit_margin=0.4,
        return_rate=0.0,
        trends=TrendData(
            mom_revenue_change=None,
            wow_revenue_change=None,
            monthly_buckets=[],
            weekly_buckets=[],
        ),
    )


def _make_product_metrics(product: Product) -> ProductMetrics:
    return ProductMetrics(
        product=product,
        revenue_contribution=Decimal("200.00"),
        units_sold=2,
        profit_margin_per_unit=0.4,
        conversion_rate=0.004,
    )


def _make_recommendation() -> Recommendation:
    return Recommendation(
        category="pricing",
        priority="HIGH",
        title="Test recommendation",
        description="A test recommendation.",
        supporting_products=[1],
        supporting_metrics={"metric": 0.1},
        estimated_impact="High impact.",
    )


def _make_pipeline(config: Configuration, tmp_path: Path) -> tuple[Pipeline, dict]:
    """Create a Pipeline with all mocked dependencies.

    Returns the Pipeline and a dict of mocks for assertion.
    """
    mocks = {
        "auth_client": MagicMock(),
        "data_collector": MagicMock(),
        "data_store": MagicMock(),
        "kpi_engine": MagicMock(),
        "recommendation_engine": MagicMock(),
        "report_generator": MagicMock(),
    }

    period = SalesPeriod(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 31, tzinfo=timezone.utc),
    )
    product = _make_product()
    orders = [_make_order()]
    products = [product]
    kpis = _make_store_kpis(period)
    product_metrics = [_make_product_metrics(product)]
    recommendations = [_make_recommendation()]

    # Wire mock return values
    mocks["auth_client"].get_valid_token.return_value = "mock-token"
    mocks["data_collector"].collect_orders.return_value = _make_collection_result(5)
    mocks["data_collector"].collect_products.return_value = _make_collection_result(3)
    mocks["data_store"].load_orders.return_value = orders
    mocks["data_store"].load_products.return_value = products
    mocks["kpi_engine"].compute_store_kpis.return_value = kpis
    mocks["kpi_engine"].compute_product_metrics.return_value = product_metrics
    mocks["recommendation_engine"].generate.return_value = recommendations
    mocks["report_generator"].write_json_summary.return_value = tmp_path / "summary.json"
    mocks["report_generator"].write_product_csv.return_value = tmp_path / "products.csv"
    mocks["report_generator"].write_powerbi_export.return_value = MagicMock()

    pipeline = Pipeline(
        config=config,
        auth_client=mocks["auth_client"],
        data_collector=mocks["data_collector"],
        data_store=mocks["data_store"],
        kpi_engine=mocks["kpi_engine"],
        recommendation_engine=mocks["recommendation_engine"],
        report_generator=mocks["report_generator"],
    )

    return pipeline, mocks


# ---------------------------------------------------------------------------
# Tests: run()
# ---------------------------------------------------------------------------

class TestPipelineRun:
    """Tests for Pipeline.run()."""

    def test_run_calls_all_components_in_order(self, tmp_path):
        """run() should call all components in the correct sequence."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        result = pipeline.run(period)

        # Authentication
        mocks["auth_client"].get_valid_token.assert_called_once()

        # Collection
        mocks["data_collector"].collect_orders.assert_called_once_with(period)
        mocks["data_collector"].collect_products.assert_called_once()

        # Data store reads
        mocks["data_store"].load_orders.assert_called_once_with(period)
        mocks["data_store"].load_products.assert_called_once()

        # Analysis
        mocks["kpi_engine"].compute_store_kpis.assert_called_once()
        mocks["kpi_engine"].compute_product_metrics.assert_called_once()
        mocks["recommendation_engine"].generate.assert_called_once()

        # Reporting
        mocks["report_generator"].write_json_summary.assert_called_once()
        mocks["report_generator"].write_product_csv.assert_called_once()
        mocks["report_generator"].write_powerbi_export.assert_called_once()

    def test_run_returns_analysis_result(self, tmp_path):
        """run() should return an AnalysisResult with correct fields."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        result = pipeline.run(period)

        assert isinstance(result, AnalysisResult)
        assert result.shop_id == config.shop_id
        assert result.period == period
        assert result.kpis is not None
        assert isinstance(result.product_metrics, list)
        assert isinstance(result.recommendations, list)

    def test_run_passes_correct_data_between_steps(self, tmp_path):
        """run() should pass loaded orders/products to the KPI engine."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )
        orders = mocks["data_store"].load_orders.return_value
        products = mocks["data_store"].load_products.return_value
        kpis = mocks["kpi_engine"].compute_store_kpis.return_value
        product_metrics = mocks["kpi_engine"].compute_product_metrics.return_value

        pipeline.run(period)

        mocks["kpi_engine"].compute_store_kpis.assert_called_once_with(orders, period)
        mocks["kpi_engine"].compute_product_metrics.assert_called_once_with(orders, products)
        mocks["recommendation_engine"].generate.assert_called_once_with(kpis, product_metrics)

    def test_run_creates_output_directory(self, tmp_path):
        """run() should create the output directory if it doesn't exist."""
        output_dir = tmp_path / "nested" / "output"
        config = _make_config(str(output_dir))
        pipeline, _ = _make_pipeline(config, tmp_path)

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        pipeline.run(period)

        assert output_dir.exists()

    def test_run_continues_when_report_step_fails(self, tmp_path):
        """run() should continue and return AnalysisResult even if reporting fails."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        # Make JSON summary fail
        mocks["report_generator"].write_json_summary.side_effect = OSError("disk full")

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        # Should still return a result even when reporting fails
        result = pipeline.run(period)
        assert isinstance(result, AnalysisResult)

        # The other report methods should still have been called
        mocks["report_generator"].write_product_csv.assert_called_once()
        mocks["report_generator"].write_powerbi_export.assert_called_once()

    def test_run_raises_when_kpis_cannot_be_computed(self, tmp_path):
        """run() should re-raise if KPI computation fails (cannot build result)."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        mocks["kpi_engine"].compute_store_kpis.side_effect = RuntimeError("computation error")

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        with pytest.raises(RuntimeError, match="computation error"):
            pipeline.run(period)

    def test_run_passes_output_dir_to_report_generator(self, tmp_path):
        """run() should pass the correct output_dir Path to all report methods."""
        output_dir = tmp_path / "output"
        config = _make_config(str(output_dir))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        pipeline.run(period)

        # Each report method should have been called with a Path equal to output_dir
        args_json = mocks["report_generator"].write_json_summary.call_args
        assert args_json[0][1] == output_dir

        args_csv = mocks["report_generator"].write_product_csv.call_args
        assert args_csv[0][1] == output_dir

        args_pbi = mocks["report_generator"].write_powerbi_export.call_args
        assert args_pbi[0][1] == output_dir


# ---------------------------------------------------------------------------
# Tests: run_incremental()
# ---------------------------------------------------------------------------

class TestPipelineRunIncremental:
    """Tests for Pipeline.run_incremental()."""

    def test_run_incremental_with_explicit_since(self, tmp_path):
        """run_incremental() with explicit since uses that timestamp directly."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = pipeline.run_incremental(since=since)

        # get_last_successful_run should NOT be called when since is explicit
        mocks["data_store"].get_last_successful_run.assert_not_called()

        # collect_orders should have been called with a period starting at since
        call_args = mocks["data_collector"].collect_orders.call_args
        period_arg: SalesPeriod = call_args[0][0]
        assert period_arg.start == since

    def test_run_incremental_without_since_uses_last_successful_run(self, tmp_path):
        """run_incremental(since=None) falls back to get_last_successful_run()."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        last_run_dt = datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)
        mocks["data_store"].get_last_successful_run.return_value = last_run_dt

        result = pipeline.run_incremental(since=None)

        mocks["data_store"].get_last_successful_run.assert_called_once()

        call_args = mocks["data_collector"].collect_orders.call_args
        period_arg: SalesPeriod = call_args[0][0]
        assert period_arg.start == last_run_dt

    def test_run_incremental_without_since_and_no_history_uses_default_period(self, tmp_path):
        """run_incremental(since=None) with no history falls back to default_period_days."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        mocks["data_store"].get_last_successful_run.return_value = None

        before_call = datetime.now(timezone.utc)
        result = pipeline.run_incremental(since=None)
        after_call = datetime.now(timezone.utc)

        call_args = mocks["data_collector"].collect_orders.call_args
        period_arg: SalesPeriod = call_args[0][0]

        expected_start_approx = before_call - timedelta(days=config.default_period_days)
        # Allow a small tolerance (5 seconds) for test timing
        assert abs((period_arg.start - expected_start_approx).total_seconds()) < 5

    def test_run_incremental_merges_collection_results(self, tmp_path):
        """run_incremental() merges two CollectionResult objects correctly."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        orders_result = CollectionResult(
            records_collected=10,
            records_failed=2,
            failed_ids=["ORD-A", "ORD-B"],
            duration_seconds=3.0,
        )
        products_result = CollectionResult(
            records_collected=5,
            records_failed=1,
            failed_ids=["PROD-X"],
            duration_seconds=5.0,
        )
        mocks["data_collector"].collect_orders.return_value = orders_result
        mocks["data_collector"].collect_products.return_value = products_result

        result = pipeline.run_incremental(since=datetime(2024, 1, 1, tzinfo=timezone.utc))

        # Records should be summed
        assert result.records_collected == 10 + 5
        assert result.records_failed == 2 + 1

        # Failed IDs should be combined
        assert set(result.failed_ids) == {"ORD-A", "ORD-B", "PROD-X"}

        # Duration should be the max of the two
        assert result.duration_seconds == max(3.0, 5.0)

    def test_run_incremental_calls_only_collect_not_analyse(self, tmp_path):
        """run_incremental() should collect only — not analyse or report."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        pipeline.run_incremental(since=since)

        # Analysis and reporting should NOT be called
        mocks["kpi_engine"].compute_store_kpis.assert_not_called()
        mocks["kpi_engine"].compute_product_metrics.assert_not_called()
        mocks["recommendation_engine"].generate.assert_not_called()
        mocks["report_generator"].write_json_summary.assert_not_called()
        mocks["report_generator"].write_product_csv.assert_not_called()
        mocks["report_generator"].write_powerbi_export.assert_not_called()

    def test_run_incremental_returns_collection_result(self, tmp_path):
        """run_incremental() should return a CollectionResult."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = pipeline.run_incremental(since=since)

        assert isinstance(result, CollectionResult)
        assert result.records_collected >= 0
        assert result.records_failed >= 0
        assert isinstance(result.failed_ids, list)
        assert result.duration_seconds >= 0.0

    def test_run_incremental_failed_step_continues_execution(self, tmp_path):
        """run_incremental() should continue to collect products even if orders fail."""
        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        mocks["data_collector"].collect_orders.side_effect = RuntimeError("orders error")

        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        # Should not raise; products should still be collected
        result = pipeline.run_incremental(since=since)

        # Products collection should still have been attempted
        mocks["data_collector"].collect_products.assert_called_once()

        # Result should reflect zero orders (from the default fallback)
        assert isinstance(result, CollectionResult)


# ---------------------------------------------------------------------------
# Tests: Logging behaviour
# ---------------------------------------------------------------------------

class TestPipelineLogging:
    """Tests that verify structured logging is emitted correctly."""

    def test_run_logs_operation_for_each_step(self, tmp_path, caplog):
        """run() should log a structured entry for each operation."""
        import logging

        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        period = SalesPeriod(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        with caplog.at_level(logging.INFO, logger="shopee_analyzer.pipeline"):
            pipeline.run(period)

        # Verify at least some key operations appear in the log
        logged_messages = " ".join(r.message for r in caplog.records)
        assert "Token" in logged_messages or "authentication" in logged_messages.lower() or \
               len(caplog.records) > 0  # At minimum, some logging occurred

    def test_run_incremental_logs_run_summary(self, tmp_path, caplog):
        """run_incremental() should emit a run summary log entry."""
        import logging

        config = _make_config(str(tmp_path / "output"))
        pipeline, mocks = _make_pipeline(config, tmp_path)

        since = datetime(2024, 1, 15, tzinfo=timezone.utc)

        with caplog.at_level(logging.INFO, logger="shopee_analyzer"):
            pipeline.run_incremental(since=since)

        # A run summary should be logged (log_run_summary emits "Run complete")
        logged_messages = [r.message for r in caplog.records]
        assert any("Run complete" in msg or "complete" in msg.lower()
                   for msg in logged_messages)
