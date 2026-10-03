"""Unit tests for shopee_analyzer.report_generator.

Covers:
  - write_json_summary: file created, required keys, naming, Decimal serialisation
  - write_product_csv: one row per product, correct columns, overwrite warning
  - write_powerbi_export: four files produced, correct columns, Date_Dim coverage
  - File overwrite warning (Requirement 5.5)
  - ReportGenerationError on write failure

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.4
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from shopee_analyzer.exceptions import ReportGenerationError
from shopee_analyzer.models import (
    AnalysisResult,
    Order,
    OrderItem,
    PeriodBucket,
    Product,
    ProductMetrics,
    Recommendation,
    SalesPeriod,
    StoreKPIs,
    TrendData,
)
from shopee_analyzer.report_generator import PowerBIExportPaths, ReportGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_product(pid: int = 1) -> Product:
    return Product(
        product_id=pid,
        name=f"Product {pid}",
        category="Electronics",
        current_price=Decimal("99.99"),
        stock_quantity=50,
        review_count=20,
        average_rating=4.5,
        total_units_sold=200,
        total_views=1000,
    )


def _make_product_metrics(pid: int = 1) -> ProductMetrics:
    return ProductMetrics(
        product=_make_product(pid),
        revenue_contribution=Decimal("1999.80"),
        units_sold=20,
        profit_margin_per_unit=0.30,
        conversion_rate=0.02,
    )


def _make_recommendation(pid: int = 1) -> Recommendation:
    return Recommendation(
        category="pricing",
        priority="HIGH",
        title="Lower price",
        description="Reduce price to improve conversion",
        supporting_products=[pid],
        supporting_metrics={"profit_margin": 0.08},
        estimated_impact="~15% revenue uplift",
    )


def _make_analysis_result(
    shop_id: int = 67890,
    start: datetime | None = None,
    end: datetime | None = None,
) -> AnalysisResult:
    start = start or datetime(2024, 1, 1)
    end = end or datetime(2024, 1, 31)
    period = SalesPeriod(start=start, end=end)
    trends = TrendData(
        mom_revenue_change=0.05,
        wow_revenue_change=0.02,
        monthly_buckets=[
            PeriodBucket(
                label="2024-01",
                start=start,
                end=end,
                revenue=Decimal("9999.00"),
                order_count=100,
            )
        ],
        weekly_buckets=[],
    )
    kpis = StoreKPIs(
        period=period,
        total_revenue=Decimal("9999.00"),
        total_orders=100,
        total_units_sold=300,
        average_order_value=Decimal("99.99"),
        gross_profit_margin=0.25,
        return_rate=0.03,
        trends=trends,
    )
    return AnalysisResult(
        shop_id=shop_id,
        period=period,
        kpis=kpis,
        product_metrics=[_make_product_metrics(1), _make_product_metrics(2)],
        recommendations=[_make_recommendation(1)],
    )


@pytest.fixture
def result() -> AnalysisResult:
    return _make_analysis_result()


@pytest.fixture
def generator() -> ReportGenerator:
    return ReportGenerator()


# ---------------------------------------------------------------------------
# Tests: write_json_summary
# ---------------------------------------------------------------------------

class TestWriteJsonSummary:
    def test_creates_file(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        assert path.exists()

    def test_returns_path_object(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        assert isinstance(path, Path)

    def test_filename_convention(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        assert path.name == "67890_2024-01-01_2024-01-31_summary.json"

    def test_required_top_level_keys(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("kpis", "product_metrics", "recommendations", "period", "shop_id"):
            assert key in data, f"Missing top-level key: {key}"

    def test_shop_id_value(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["shop_id"] == result.shop_id

    def test_product_metrics_count(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["product_metrics"]) == len(result.product_metrics)

    def test_recommendations_count(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        path = generator.write_json_summary(result, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["recommendations"]) == len(result.recommendations)

    def test_decimal_serialised_as_string(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        """Decimal values must be serialised as strings, not raw floats."""
        path = generator.write_json_summary(result, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        # total_revenue is a Decimal; the implementation converts it to str()
        assert isinstance(data["kpis"]["total_revenue"], str)

    def test_creates_output_dir_if_missing(self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult):
        nested = tmp_path / "nested" / "output"
        generator.write_json_summary(result, nested)
        assert nested.exists()

    def test_overwrite_logs_warning(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult, caplog
    ):
        generator.write_json_summary(result, tmp_path)
        with caplog.at_level(logging.WARNING, logger="shopee_analyzer.report_generator"):
            generator.write_json_summary(result, tmp_path)
        assert any("Overwriting" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: write_product_csv
# ---------------------------------------------------------------------------

class TestWriteProductCsv:
    def test_creates_file(self, tmp_path: Path, generator: ReportGenerator):
        metrics = [_make_product_metrics(1), _make_product_metrics(2)]
        path = generator.write_product_csv(metrics, tmp_path)
        assert path.exists()

    def test_row_count_matches_products(self, tmp_path: Path, generator: ReportGenerator):
        metrics = [_make_product_metrics(1), _make_product_metrics(2), _make_product_metrics(3)]
        path = generator.write_product_csv(metrics, tmp_path)
        df = pd.read_csv(path)
        assert len(df) == 3

    def test_required_columns_present(self, tmp_path: Path, generator: ReportGenerator):
        required_cols = {
            "product_id", "product_name", "category", "current_price",
            "stock_quantity", "review_count", "average_rating", "total_units_sold",
            "total_views", "revenue_contribution", "units_sold",
            "profit_margin_per_unit", "conversion_rate",
        }
        path = generator.write_product_csv([_make_product_metrics(1)], tmp_path)
        df = pd.read_csv(path)
        assert required_cols.issubset(set(df.columns))

    def test_empty_metrics_produces_header_only(self, tmp_path: Path, generator: ReportGenerator):
        path = generator.write_product_csv([], tmp_path)
        df = pd.read_csv(path)
        assert len(df) == 0
        assert "product_id" in df.columns

    def test_overwrite_logs_warning(
        self, tmp_path: Path, generator: ReportGenerator, caplog
    ):
        metrics = [_make_product_metrics(1)]
        generator.write_product_csv(metrics, tmp_path)
        with caplog.at_level(logging.WARNING, logger="shopee_analyzer.report_generator"):
            generator.write_product_csv(metrics, tmp_path)
        assert any("Overwriting" in r.message for r in caplog.records)

    def test_returns_path_object(self, tmp_path: Path, generator: ReportGenerator):
        path = generator.write_product_csv([_make_product_metrics(1)], tmp_path)
        assert isinstance(path, Path)


# ---------------------------------------------------------------------------
# Tests: write_powerbi_export
# ---------------------------------------------------------------------------

class TestWritePowerBIExport:
    def test_returns_powerbi_export_paths(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        assert isinstance(paths, PowerBIExportPaths)

    def test_all_four_files_created(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        assert paths.sales_fact.exists()
        assert paths.product_dim.exists()
        assert paths.date_dim.exists()
        assert paths.recommendation_fact.exists()

    def test_sales_fact_filename_convention(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        assert paths.sales_fact.name == "67890_2024-01-01_2024-01-31_Sales_Fact.csv"

    def test_product_dim_filename_convention(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        assert paths.product_dim.name == "67890_2024-01-01_2024-01-31_Product_Dim.csv"

    def test_date_dim_filename_convention(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        assert paths.date_dim.name == "67890_2024-01-01_2024-01-31_Date_Dim.csv"

    def test_recommendation_fact_filename_convention(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        assert paths.recommendation_fact.name == "67890_2024-01-01_2024-01-31_Recommendation_Fact.csv"

    def test_product_dim_columns(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.product_dim)
        required = {"product_id", "product_name", "category", "current_price",
                    "average_rating", "total_units_sold"}
        assert required.issubset(set(df.columns))

    def test_product_dim_row_count(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.product_dim)
        assert len(df) == len(result.product_metrics)

    def test_date_dim_columns(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        required = {
            "date_key", "full_date", "day_of_week", "day_name", "week_number",
            "fiscal_week", "month", "month_name", "fiscal_month", "quarter",
            "fiscal_quarter", "year", "fiscal_year", "is_weekend",
        }
        assert required.issubset(set(df.columns))

    def test_date_dim_row_count_matches_period(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        """Date_Dim must have exactly one row per calendar date in the period."""
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        # Jan 1–31 inclusive = 31 days
        assert len(df) == 31

    def test_date_dim_no_null_fiscal_fields(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        """Fiscal columns must be non-null (Requirement 6.4)."""
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        fiscal_cols = ["fiscal_week", "fiscal_month", "fiscal_quarter", "fiscal_year",
                       "day_of_week", "is_weekend"]
        for col in fiscal_cols:
            assert not df[col].isnull().any(), f"Column {col} has null values"

    def test_recommendation_fact_columns(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.recommendation_fact)
        required = {
            "recommendation_id", "category", "priority", "title",
            "description", "product_ids", "analysis_date_key",
        }
        assert required.issubset(set(df.columns))

    def test_recommendation_fact_row_count(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.recommendation_fact)
        assert len(df) == len(result.recommendations)

    def test_overwrite_logs_warning(
        self,
        tmp_path: Path,
        generator: ReportGenerator,
        result: AnalysisResult,
        caplog,
    ):
        generator.write_powerbi_export(result, tmp_path)
        with caplog.at_level(logging.WARNING, logger="shopee_analyzer.report_generator"):
            generator.write_powerbi_export(result, tmp_path)
        assert any("Overwriting" in r.message for r in caplog.records)

    def test_sales_fact_columns(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.sales_fact)
        required = {
            "order_id", "order_date_key", "product_id", "quantity",
            "unit_price", "line_total", "shipping_fee", "discount", "order_status",
        }
        assert required.issubset(set(df.columns))

    def test_sales_fact_populated_from_raw_orders(
        self, tmp_path: Path, generator: ReportGenerator, result: AnalysisResult
    ):
        """When _raw_orders is set, Sales_Fact rows are derived from real orders."""
        orders = [
            Order(
                order_id="ORD001",
                status="COMPLETED",
                items=[
                    OrderItem(product_id=1, product_name="Product 1", quantity=2, unit_price=Decimal("49.99")),
                    OrderItem(product_id=2, product_name="Product 2", quantity=1, unit_price=Decimal("99.99")),
                ],
                total_payment=Decimal("199.97"),
                shipping_fee=Decimal("5.00"),
                discount_total=Decimal("0.00"),
                created_at=datetime(2024, 1, 15, 10, 0, 0),
            )
        ]
        result._raw_orders = orders  # type: ignore[attr-defined]
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.sales_fact)
        # 2 items in the one order → 2 rows
        assert len(df) == 2
        assert df["order_date_key"].iloc[0] == 20240115


# ---------------------------------------------------------------------------
# Tests: filename convention (_file_stem helper)
# ---------------------------------------------------------------------------

class TestFilenameConvention:
    def test_json_summary_naming(self, tmp_path: Path, generator: ReportGenerator):
        result = _make_analysis_result(
            shop_id=67890,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 31),
        )
        path = generator.write_json_summary(result, tmp_path)
        assert path.name == "67890_2024-01-01_2024-01-31_summary.json"

    def test_powerbi_export_naming_all_four(self, tmp_path: Path, generator: ReportGenerator):
        result = _make_analysis_result(
            shop_id=12345,
            start=datetime(2023, 6, 15),
            end=datetime(2023, 6, 30),
        )
        paths = generator.write_powerbi_export(result, tmp_path)
        assert paths.sales_fact.name == "12345_2023-06-15_2023-06-30_Sales_Fact.csv"
        assert paths.product_dim.name == "12345_2023-06-15_2023-06-30_Product_Dim.csv"
        assert paths.date_dim.name == "12345_2023-06-15_2023-06-30_Date_Dim.csv"
        assert paths.recommendation_fact.name == "12345_2023-06-15_2023-06-30_Recommendation_Fact.csv"


# ---------------------------------------------------------------------------
# Tests: Date_Dim helpers
# ---------------------------------------------------------------------------

class TestDateDimHelpers:
    def test_single_day_period(self, tmp_path: Path, generator: ReportGenerator):
        result = _make_analysis_result(
            start=datetime(2024, 3, 5),
            end=datetime(2024, 3, 5),
        )
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        assert len(df) == 1
        assert df["date_key"].iloc[0] == 20240305

    def test_weekend_detection(self, tmp_path: Path, generator: ReportGenerator):
        # 2024-03-01 is Friday (weekday 4), 2024-03-02 is Saturday, 2024-03-03 is Sunday
        result = _make_analysis_result(
            start=datetime(2024, 3, 1),
            end=datetime(2024, 3, 3),
        )
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        assert bool(df.loc[df["date_key"] == 20240301, "is_weekend"].iloc[0]) is False
        assert bool(df.loc[df["date_key"] == 20240302, "is_weekend"].iloc[0]) is True
        assert bool(df.loc[df["date_key"] == 20240303, "is_weekend"].iloc[0]) is True

    def test_month_name_correctness(self, tmp_path: Path, generator: ReportGenerator):
        result = _make_analysis_result(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 1),
        )
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        assert df["month_name"].iloc[0] == "January"

    def test_day_of_week_monday_is_zero(self, tmp_path: Path, generator: ReportGenerator):
        # 2024-01-01 is Monday
        result = _make_analysis_result(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 1),
        )
        paths = generator.write_powerbi_export(result, tmp_path)
        df = pd.read_csv(paths.date_dim)
        assert df["day_of_week"].iloc[0] == 0  # Monday = 0

    def test_quarter_assignment(self, tmp_path: Path, generator: ReportGenerator):
        """Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec."""
        for month, expected_q in [(1, 1), (4, 2), (7, 3), (10, 4)]:
            result = _make_analysis_result(
                start=datetime(2024, month, 1),
                end=datetime(2024, month, 1),
            )
            paths = generator.write_powerbi_export(result, tmp_path)
            df = pd.read_csv(paths.date_dim)
            assert df["quarter"].iloc[0] == expected_q, f"Month {month} should be Q{expected_q}"


# ---------------------------------------------------------------------------
# Property-Based Tests: Date_Dim coverage and column completeness (Property 17)
# Validates: Requirements 6.4
# ---------------------------------------------------------------------------

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st


@st.composite
def date_range_strategy(draw):
    """Generate (start_date, end_date) pairs within 2020-2024, max 365-day span."""
    min_date = date(2020, 1, 1)
    max_date = date(2024, 12, 31)
    start = draw(st.dates(min_value=min_date, max_value=max_date))
    # end_date >= start, but at most 365 days ahead (and still within max_date)
    max_end = min(start + timedelta(days=365), max_date)
    end = draw(st.dates(min_value=start, max_value=max_end))
    return (start, end)


# Feature: shopee-sales-analyzer, Property 17: Date_Dim coverage and column completeness
@given(date_range_strategy())
@settings(max_examples=100)
def test_date_dim_coverage_and_column_completeness(date_range):
    """**Validates: Requirements 6.4**

    For any date range, the generated Date_Dim table should contain exactly one
    row per calendar date in the range, and each row should have non-null values
    for all time-intelligence columns.
    """
    from shopee_analyzer.report_generator import _build_date_dim_rows
    from datetime import datetime

    start, end = date_range
    # Convert to datetime for _build_date_dim_rows
    start_dt = datetime(start.year, start.month, start.day)
    end_dt = datetime(end.year, end.month, end.day)
    rows = _build_date_dim_rows(start_dt, end_dt)

    # Assert exactly one row per calendar date
    expected_count = (end - start).days + 1
    assert len(rows) == expected_count

    # Assert all required time-intelligence columns are non-null
    required_cols = {
        "date_key", "full_date", "day_of_week", "day_name", "week_number",
        "fiscal_week", "month", "month_name", "fiscal_month", "quarter",
        "fiscal_quarter", "year", "fiscal_year", "is_weekend",
    }
    for row in rows:
        for col in required_cols:
            assert col in row, f"Missing column: {col}"
            assert row[col] is not None, f"Column {col} is None in row {row}"


# ---------------------------------------------------------------------------
# Example-based tests for _build_date_dim_rows directly
# ---------------------------------------------------------------------------

class TestBuildDateDimRowsExamples:
    def test_single_day_produces_one_row(self):
        """Single day: start == end → exactly 1 row, all columns non-null."""
        from shopee_analyzer.report_generator import _build_date_dim_rows
        from datetime import datetime

        dt = datetime(2024, 3, 15)
        rows = _build_date_dim_rows(dt, dt)
        assert len(rows) == 1
        required_cols = {
            "date_key", "full_date", "day_of_week", "day_name", "week_number",
            "fiscal_week", "month", "month_name", "fiscal_month", "quarter",
            "fiscal_quarter", "year", "fiscal_year", "is_weekend",
        }
        for col in required_cols:
            assert col in rows[0], f"Missing column: {col}"
            assert rows[0][col] is not None, f"Column {col} is None"

    def test_full_year_2024_is_leap_year_366_rows(self):
        """Full year 2024 (leap year) → 366 rows."""
        from shopee_analyzer.report_generator import _build_date_dim_rows
        from datetime import datetime

        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)
        rows = _build_date_dim_rows(start, end)
        assert len(rows) == 366

    def test_saturday_is_weekend(self):
        """2024-03-02 is a Saturday → is_weekend=True."""
        from shopee_analyzer.report_generator import _build_date_dim_rows
        from datetime import datetime

        dt = datetime(2024, 3, 2)
        rows = _build_date_dim_rows(dt, dt)
        assert len(rows) == 1
        assert rows[0]["is_weekend"] is True

    def test_monday_is_not_weekend(self):
        """2024-03-04 is a Monday → is_weekend=False."""
        from shopee_analyzer.report_generator import _build_date_dim_rows
        from datetime import datetime

        dt = datetime(2024, 3, 4)
        rows = _build_date_dim_rows(dt, dt)
        assert len(rows) == 1
        assert rows[0]["is_weekend"] is False
