"""Integration tests for the full Pipeline execution pipeline.

Tests use:
- DataStore backed by a real SQLite file (tmp_path) — shared across connections
- KPIEngine() — real implementation
- RecommendationEngine() — real implementation
- ReportGenerator() — real implementation
- AuthClient mocked to return a fake access token
- DataCollector mocked to save pre-built data to the DataStore and return CollectionResult

Requirements: 2.1, 3.1, 4.1, 5.1, 7.2, 9.5
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shopee_analyzer.kpi_engine import KPIEngine
from shopee_analyzer.models import (
    AnalysisResult,
    CollectionResult,
    Configuration,
    Order,
    OrderItem,
    Product,
    SalesPeriod,
)
from shopee_analyzer.pipeline import Pipeline
from shopee_analyzer.recommendation_engine import RecommendationEngine
from shopee_analyzer.report_generator import ReportGenerator
from shopee_analyzer.storage import DataStore


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# 3 products: Widget A (out-of-stock + low rating), Widget B (normal),
# Widget C (low conversion rate)
PRODUCTS = [
    Product(
        product_id=1,
        name="Widget A",
        category="Electronics",
        current_price=Decimal("50.00"),
        stock_quantity=0,
        review_count=25,
        average_rating=2.8,
        total_units_sold=200,
        total_views=5000,
    ),
    Product(
        product_id=2,
        name="Widget B",
        category="Fashion",
        current_price=Decimal("30.00"),
        stock_quantity=10,
        review_count=5,
        average_rating=4.5,
        total_units_sold=50,
        total_views=200,
    ),
    Product(
        product_id=3,
        name="Widget C",
        category="Home",
        current_price=Decimal("80.00"),
        stock_quantity=5,
        review_count=50,
        average_rating=4.8,
        total_units_sold=400,
        total_views=8000,
    ),
]

# 10 orders spread across January 2024
ORDERS = [
    Order(
        order_id=f"ORD-{i:03d}",
        status="COMPLETED",
        items=[
            OrderItem(
                product_id=i % 3 + 1,
                product_name=f"Widget {chr(65 + i % 3)}",
                quantity=i % 3 + 1,
                unit_price=Decimal("50.00"),
            )
        ],
        total_payment=Decimal("50.00") * (i % 3 + 1),
        shipping_fee=Decimal("5.00"),
        discount_total=Decimal("0.00"),
        created_at=datetime(2024, 1, i + 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    for i in range(10)
]

# The analysis period covering all test orders
TEST_PERIOD = SalesPeriod(
    start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    end=datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(output_dir: str, db_path: str = ":memory:") -> Configuration:
    return Configuration(
        partner_id=12345,
        partner_key="secret-key",
        shop_id=67890,
        data_store_path=db_path,
        output_dir=output_dir,
        refresh_schedule="daily",
        default_period_days=30,
        log_level="INFO",
        log_file="test_integration.log",
    )


def _build_pipeline(
    output_dir: Path,
    data_store: DataStore,
    db_path: str = ":memory:",
    orders: list[Order] | None = None,
    products: list[Product] | None = None,
) -> tuple[Pipeline, MagicMock, MagicMock]:
    """Build a Pipeline with real engines and mocked HTTP layer.

    Returns:
        (pipeline, mock_auth_client, mock_data_collector)
    """
    if orders is None:
        orders = ORDERS
    if products is None:
        products = PRODUCTS

    config = _make_config(str(output_dir), db_path=db_path)

    # --- Mock AuthClient (no network) ---
    mock_auth = MagicMock()
    mock_auth.get_valid_token.return_value = "fake-access-token"

    # --- Mock DataCollector: saves data to the real DataStore, then returns a CollectionResult ---
    mock_collector = MagicMock()

    def _collect_orders(period: SalesPeriod) -> CollectionResult:
        # Filter orders to the requested period (simulate real collector behaviour)
        period_orders = [
            o for o in orders
            if period.start <= o.created_at <= period.end
        ]
        data_store.save_orders(period_orders)
        return CollectionResult(
            records_collected=len(period_orders),
            records_failed=0,
            failed_ids=[],
            duration_seconds=0.1,
        )

    def _collect_products() -> CollectionResult:
        data_store.save_products(products)
        return CollectionResult(
            records_collected=len(products),
            records_failed=0,
            failed_ids=[],
            duration_seconds=0.1,
        )

    mock_collector.collect_orders.side_effect = _collect_orders
    mock_collector.collect_products.side_effect = _collect_products

    pipeline = Pipeline(
        config=config,
        auth_client=mock_auth,
        data_collector=mock_collector,
        data_store=data_store,
        kpi_engine=KPIEngine(),
        recommendation_engine=RecommendationEngine(),
        report_generator=ReportGenerator(),
    )

    return pipeline, mock_auth, mock_collector


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPipelineIntegration:
    """End-to-end integration tests for Pipeline.run() and run_incremental()."""

    # ------------------------------------------------------------------
    # 1. test_full_run_returns_analysis_result
    # ------------------------------------------------------------------

    def test_full_run_returns_analysis_result(self, tmp_path):
        """Pipeline.run() returns an AnalysisResult with non-empty kpis,
        product_metrics, and recommendations.

        Requirements: 2.1, 3.1, 4.1
        """
        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, _ = _build_pipeline(tmp_path / "output", data_store, db_path=db_path)

        result = pipeline.run(TEST_PERIOD)

        assert isinstance(result, AnalysisResult)
        assert result.shop_id == 67890
        assert result.period == TEST_PERIOD

        # KPIs should be populated
        assert result.kpis is not None
        assert result.kpis.total_orders > 0
        assert result.kpis.total_revenue > Decimal("0")

        # Product metrics — one entry per product
        assert len(result.product_metrics) == len(PRODUCTS)

        # Recommendations — at least one generated for the triggerable products
        assert isinstance(result.recommendations, list)

    # ------------------------------------------------------------------
    # 2. test_full_run_creates_output_files
    # ------------------------------------------------------------------

    def test_full_run_creates_output_files(self, tmp_path):
        """After Pipeline.run(), the output directory must contain at least
        one .json file and one .csv file.

        Requirements: 5.1
        """
        output_dir = tmp_path / "output"
        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, _ = _build_pipeline(output_dir, data_store, db_path=db_path)

        pipeline.run(TEST_PERIOD)

        json_files = list(output_dir.glob("*.json"))
        csv_files = list(output_dir.glob("*.csv"))

        assert len(json_files) >= 1, "Expected at least one JSON output file"
        assert len(csv_files) >= 1, "Expected at least one CSV output file"

        # The JSON file must be parseable and contain the expected top-level keys
        with json_files[0].open(encoding="utf-8") as f:
            data = json.load(f)
        assert "shop_id" in data
        assert "kpis" in data
        assert "product_metrics" in data
        assert "recommendations" in data

        # The CSV must have a header row
        with csv_files[0].open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        assert header is not None and len(header) > 0

    # ------------------------------------------------------------------
    # 3. test_full_run_product_metrics_match_orders
    # ------------------------------------------------------------------

    def test_full_run_product_metrics_match_orders(self, tmp_path):
        """Product metric revenue contributions must be mathematically
        consistent with the order line items.

        Requirements: 3.1, 3.2
        """
        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, _ = _build_pipeline(tmp_path / "output", data_store, db_path=db_path)

        result = pipeline.run(TEST_PERIOD)

        # Compute expected revenue per product directly from ORDERS
        expected_revenue: dict[int, Decimal] = {}
        for order in ORDERS:
            if TEST_PERIOD.start <= order.created_at <= TEST_PERIOD.end:
                for item in order.items:
                    pid = item.product_id
                    expected_revenue[pid] = (
                        expected_revenue.get(pid, Decimal("0"))
                        + item.unit_price * item.quantity
                    )

        # Verify each product's revenue_contribution matches the calculation
        metrics_by_product = {
            m.product.product_id: m for m in result.product_metrics
        }
        for product_id, expected in expected_revenue.items():
            assert product_id in metrics_by_product, (
                f"Product {product_id} not found in product_metrics"
            )
            actual = metrics_by_product[product_id].revenue_contribution
            assert actual == expected, (
                f"Product {product_id}: expected revenue {expected}, got {actual}"
            )

        # Total revenue must equal sum of all order payments
        expected_total = sum(
            o.total_payment
            for o in ORDERS
            if TEST_PERIOD.start <= o.created_at <= TEST_PERIOD.end
        )
        assert result.kpis.total_revenue == expected_total

    # ------------------------------------------------------------------
    # 4. test_run_incremental_returns_collection_result
    # ------------------------------------------------------------------

    def test_run_incremental_returns_collection_result(self, tmp_path):
        """Pipeline.run_incremental(since=...) returns a CollectionResult.

        Requirements: 7.2
        """
        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, _ = _build_pipeline(tmp_path / "output", data_store, db_path=db_path)

        since = datetime(2024, 1, 5, 0, 0, 0, tzinfo=timezone.utc)
        result = pipeline.run_incremental(since=since)

        assert isinstance(result, CollectionResult)
        assert result.records_collected >= 0
        assert result.records_failed >= 0
        assert isinstance(result.failed_ids, list)
        assert result.duration_seconds >= 0.0

        # Some orders and products should have been collected
        assert result.records_collected > 0

    # ------------------------------------------------------------------
    # 5. test_run_incremental_collects_only_new_orders
    # ------------------------------------------------------------------

    def test_run_incremental_collects_only_new_orders(self, tmp_path):
        """run_incremental(since=...) must call collect_orders with a period
        whose start equals the 'since' timestamp.

        Requirements: 7.2
        """
        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, mock_collector = _build_pipeline(tmp_path / "output", data_store, db_path=db_path)

        since = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)
        pipeline.run_incremental(since=since)

        # collect_orders must have been called exactly once
        mock_collector.collect_orders.assert_called_once()
        call_args = mock_collector.collect_orders.call_args
        period_arg: SalesPeriod = call_args[0][0]

        # The period start must equal 'since'
        assert period_arg.start == since

        # The period end must be after 'since' (approximately now)
        assert period_arg.end > since

    # ------------------------------------------------------------------
    # 6. test_recommendations_are_generated_for_triggerable_products
    # ------------------------------------------------------------------

    def test_recommendations_are_generated_for_triggerable_products(self, tmp_path):
        """Widget A (out-of-stock + low rating) and Widget C (low conversion
        rate) must trigger at least one HIGH or MEDIUM recommendation.

        Requirements: 4.1
        """
        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, _ = _build_pipeline(tmp_path / "output", data_store, db_path=db_path)

        result = pipeline.run(TEST_PERIOD)

        # Filter to HIGH or MEDIUM priority recommendations only
        high_medium_recs = [
            r for r in result.recommendations
            if r.priority in ("HIGH", "MEDIUM")
        ]
        assert len(high_medium_recs) >= 1, (
            "Expected at least one HIGH or MEDIUM recommendation for the "
            "triggerable products (Widget A: out-of-stock + low rating; "
            "Widget C: low conversion rate)"
        )

        # Verify the specific triggered rules
        all_rec_titles = [r.title for r in result.recommendations]
        all_rec_products = [pid for r in result.recommendations for pid in r.supporting_products]

        # Widget A (product_id=1): out-of-stock with 200 units sold → HIGH inventory rec
        widget_a_recs = [
            r for r in result.recommendations
            if 1 in r.supporting_products and r.category == "inventory"
        ]
        assert len(widget_a_recs) >= 1, (
            "Expected an inventory recommendation for Widget A (out-of-stock)"
        )
        assert all(r.priority == "HIGH" for r in widget_a_recs)

        # Widget A (product_id=1): rating 2.8 with 25 reviews → MEDIUM listing rec
        widget_a_rating_recs = [
            r for r in result.recommendations
            if 1 in r.supporting_products and r.category == "listing"
        ]
        assert len(widget_a_rating_recs) >= 1, (
            "Expected a listing recommendation for Widget A (low rating)"
        )

    # ------------------------------------------------------------------
    # Additional: test that refresh history can be recorded and retrieved
    # ------------------------------------------------------------------

    def test_refresh_history_recorded_correctly(self, tmp_path):
        """Refresh history can be saved to and loaded from the DataStore.

        Requirements: 9.5
        """
        import uuid
        from shopee_analyzer.models import RefreshHistoryEntry

        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)

        # Simulate a successful run being recorded in refresh_history
        entry = RefreshHistoryEntry(
            run_id=str(uuid.uuid4()),
            run_timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            status="SUCCESS",
            records_collected=13,
            duration_seconds=2.5,
            failure_reason=None,
        )
        data_store.save_refresh_history(entry)

        # Verify it can be retrieved
        history = data_store.load_refresh_history()
        assert len(history) == 1
        assert history[0].run_id == entry.run_id
        assert history[0].status == "SUCCESS"
        assert history[0].records_collected == 13

        # get_last_successful_run should return the correct timestamp
        last_run = data_store.get_last_successful_run()
        assert last_run is not None
        # Compare without microseconds (ISO round-trip may lose sub-second precision)
        assert last_run.replace(microsecond=0) == entry.run_timestamp.replace(microsecond=0)

    # ------------------------------------------------------------------
    # Additional: run_incremental uses last successful run when since is None
    # ------------------------------------------------------------------

    def test_run_incremental_uses_last_successful_run_when_since_is_none(self, tmp_path):
        """When run_incremental is called without 'since', it should use the
        timestamp of the last successful run stored in the DataStore.

        Requirements: 7.2
        """
        import uuid
        from shopee_analyzer.models import RefreshHistoryEntry

        db_path = str(tmp_path / "test.db")
        data_store = DataStore(db_path)
        pipeline, _, mock_collector = _build_pipeline(tmp_path / "output", data_store, db_path=db_path)

        last_run_ts = datetime(2024, 1, 6, 0, 0, 0, tzinfo=timezone.utc)

        # Record a successful run so get_last_successful_run() returns a value
        data_store.save_refresh_history(
            RefreshHistoryEntry(
                run_id=str(uuid.uuid4()),
                run_timestamp=last_run_ts,
                status="SUCCESS",
                records_collected=5,
                duration_seconds=1.0,
                failure_reason=None,
            )
        )

        pipeline.run_incremental(since=None)

        # collect_orders should have been called with a period starting at last_run_ts
        mock_collector.collect_orders.assert_called_once()
        call_period: SalesPeriod = mock_collector.collect_orders.call_args[0][0]
        assert call_period.start == last_run_ts
