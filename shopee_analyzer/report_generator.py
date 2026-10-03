"""Report Generator for the Shopee Sales Analyzer.

Serialises analysis output to JSON, CSV, and Power BI-compatible star-schema
CSV files.  All public methods are deterministic given the same inputs.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.4
"""

from __future__ import annotations

import csv
import json
import logging
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from shopee_analyzer.exceptions import ReportGenerationError
from shopee_analyzer.models import (
    AnalysisResult,
    ProductMetrics,
    Recommendation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Power BI export paths dataclass
# ---------------------------------------------------------------------------

@dataclass
class PowerBIExportPaths:
    """Paths to the four star-schema CSV files produced by the Power BI export.

    Requirements: 6.1
    """

    sales_fact: Path
    product_dim: Path
    date_dim: Path
    recommendation_fact: Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_key(dt: datetime | date) -> int:
    """Convert a date or datetime to a YYYYMMDD integer date key."""
    if isinstance(dt, datetime):
        dt = dt.date()
    return int(dt.strftime("%Y%m%d"))


def _file_stem(shop_id: int, start: datetime, end: datetime, report_type: str) -> str:
    """Build the filename stem using the required naming convention.

    Pattern: {shop_id}_{start_date}_{end_date}_{report_type}

    Requirements: 5.4
    """
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    return f"{shop_id}_{start_str}_{end_str}_{report_type}"


def _warn_if_exists(path: Path) -> None:
    """Log a warning if a report file already exists (Requirement 5.5)."""
    if path.exists():
        logger.warning("Overwriting existing report file: %s", path)


# ---------------------------------------------------------------------------
# Date_Dim generation helper
# ---------------------------------------------------------------------------

def _build_date_dim_rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Generate one Date_Dim row per calendar date in [start.date(), end.date()].

    Columns produced:
        date_key, full_date, day_of_week, day_name, week_number, fiscal_week,
        month, month_name, fiscal_month, quarter, fiscal_quarter, year,
        fiscal_year, is_weekend

    Fiscal calendar assumption: fiscal year starts in January (same as
    calendar year).  Fiscal week, month, quarter, and year therefore equal
    the calendar equivalents.

    Requirements: 6.1, 6.4
    """
    rows: list[dict[str, Any]] = []
    current = start.date() if isinstance(start, datetime) else start
    finish = end.date() if isinstance(end, datetime) else end

    # Ensure at least one row even if start == end
    while current <= finish:
        iso = current.isocalendar()
        month = current.month
        quarter = (month - 1) // 3 + 1
        rows.append(
            {
                "date_key": int(current.strftime("%Y%m%d")),
                "full_date": str(current),
                "day_of_week": current.weekday(),          # 0=Mon … 6=Sun
                "day_name": current.strftime("%A"),
                "week_number": iso.week,
                "fiscal_week": iso.week,
                "month": month,
                "month_name": current.strftime("%B"),
                "fiscal_month": month,
                "quarter": quarter,
                "fiscal_quarter": quarter,
                "year": current.year,
                "fiscal_year": current.year,
                "is_weekend": current.weekday() >= 5,
            }
        )
        current += timedelta(days=1)

    return rows


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Serialises analysis output to files.

    Instantiate with no arguments.  All methods are deterministic given the
    same inputs (except for filesystem side effects).
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def write_json_summary(
        self, result: AnalysisResult, output_dir: Path
    ) -> Path:
        """Write a JSON summary of the full analysis result.

        Requirements: 5.1, 5.4, 5.5
        """
        stem = _file_stem(
            result.shop_id,
            result.period.start,
            result.period.end,
            "summary",
        )
        path = output_dir / f"{stem}.json"
        _warn_if_exists(path)

        payload: dict[str, Any] = {
            "shop_id": result.shop_id,
            "period": {
                "start": result.period.start.isoformat(),
                "end": result.period.end.isoformat(),
            },
            "kpis": {
                "total_revenue": str(result.kpis.total_revenue),
                "total_orders": result.kpis.total_orders,
                "total_units_sold": result.kpis.total_units_sold,
                "average_order_value": str(result.kpis.average_order_value),
                "gross_profit_margin": result.kpis.gross_profit_margin,
                "return_rate": result.kpis.return_rate,
            },
            "product_metrics": [
                {
                    "product_id": m.product.product_id,
                    "product_name": m.product.name,
                    "revenue_contribution": str(m.revenue_contribution),
                    "units_sold": m.units_sold,
                    "profit_margin_per_unit": m.profit_margin_per_unit,
                    "conversion_rate": m.conversion_rate,
                }
                for m in result.product_metrics
            ],
            "recommendations": [
                {
                    "category": r.category,
                    "priority": r.priority,
                    "title": r.title,
                    "description": r.description,
                    "supporting_products": r.supporting_products,
                    "estimated_impact": r.estimated_impact,
                }
                for r in result.recommendations
            ],
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            raise ReportGenerationError(
                f"Failed to write JSON summary to {path}: {exc}"
            ) from exc
        logger.info("JSON summary written: %s", path)
        return path

    def write_product_csv(
        self, metrics: list[ProductMetrics], output_dir: Path
    ) -> Path:
        """Write a CSV of all product metrics.

        One row per product; columns are all required metric fields.

        Requirements: 5.2, 5.4, 5.5
        """
        # Use a generic filename (no period info here).
        path = output_dir / "product_metrics.csv"
        _warn_if_exists(path)

        fieldnames = [
            "product_id",
            "product_name",
            "category",
            "current_price",
            "stock_quantity",
            "review_count",
            "average_rating",
            "total_units_sold",
            "total_views",
            "revenue_contribution",
            "units_sold",
            "profit_margin_per_unit",
            "conversion_rate",
        ]

        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for m in metrics:
                    writer.writerow(
                        {
                            "product_id": m.product.product_id,
                            "product_name": m.product.name,
                            "category": m.product.category,
                            "current_price": float(m.product.current_price),
                            "stock_quantity": m.product.stock_quantity,
                            "review_count": m.product.review_count,
                            "average_rating": m.product.average_rating,
                            "total_units_sold": m.product.total_units_sold,
                            "total_views": m.product.total_views,
                            "revenue_contribution": float(m.revenue_contribution),
                            "units_sold": m.units_sold,
                            "profit_margin_per_unit": m.profit_margin_per_unit,
                            "conversion_rate": m.conversion_rate,
                        }
                    )
        except OSError as exc:
            raise ReportGenerationError(
                f"Failed to write product CSV to {path}: {exc}"
            ) from exc

        logger.info("Product CSV written: %s", path)
        return path

    def write_powerbi_export(
        self, result: AnalysisResult, output_dir: Path
    ) -> PowerBIExportPaths:
        """Write the four Power BI star-schema CSV files.

        Files produced:
          - {stem}_Sales_Fact.csv
          - {stem}_Product_Dim.csv
          - {stem}_Date_Dim.csv
          - {stem}_Recommendation_Fact.csv

        Each file is named with the standard pattern
        {shop_id}_{start_date}_{end_date}_{table_name}.csv.

        Requirements: 6.1, 5.4, 5.5
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        shop_id = result.shop_id
        start = result.period.start
        end = result.period.end

        sales_fact_path = self._write_sales_fact(result, output_dir, shop_id, start, end)
        product_dim_path = self._write_product_dim(result, output_dir, shop_id, start, end)
        date_dim_path = self._write_date_dim(result, output_dir, shop_id, start, end)
        rec_fact_path = self._write_recommendation_fact(result, output_dir, shop_id, start, end)

        return PowerBIExportPaths(
            sales_fact=sales_fact_path,
            product_dim=product_dim_path,
            date_dim=date_dim_path,
            recommendation_fact=rec_fact_path,
        )

    # ------------------------------------------------------------------
    # Private star-schema writers
    # ------------------------------------------------------------------

    def _write_sales_fact(
        self,
        result: AnalysisResult,
        output_dir: Path,
        shop_id: int,
        start: datetime,
        end: datetime,
    ) -> Path:
        """Write Sales_Fact.csv.

        Columns: order_id, order_date_key, product_id, quantity, unit_price,
                 line_total, shipping_fee, discount, order_status
        """
        stem = _file_stem(shop_id, start, end, "Sales_Fact")
        path = output_dir / f"{stem}.csv"
        _warn_if_exists(path)

        fieldnames = [
            "order_id",
            "order_date_key",
            "product_id",
            "quantity",
            "unit_price",
            "line_total",
            "shipping_fee",
            "discount",
            "order_status",
        ]

        # Build rows from the KPI engine's stored orders via product_metrics.
        # Since AnalysisResult does not carry raw orders directly, we derive
        # per-product-per-order facts from the available data.  When no raw
        # orders are available (e.g. the result was constructed directly for
        # testing), we emit one summary row per product metric.
        rows: list[dict[str, Any]] = []

        # Attempt to get raw orders if stored on the result (optional attr).
        raw_orders = getattr(result, "_raw_orders", None)

        if raw_orders:
            for order in raw_orders:
                order_date_key = _date_key(order.created_at)
                # Per-order shipping and discount split evenly across items.
                n_items = len(order.items) or 1
                shipping_per_item = float(order.shipping_fee) / n_items
                discount_per_item = float(order.discount_total) / n_items

                for item in order.items:
                    rows.append(
                        {
                            "order_id": order.order_id,
                            "order_date_key": order_date_key,
                            "product_id": item.product_id,
                            "quantity": item.quantity,
                            "unit_price": float(item.unit_price),
                            "line_total": float(item.unit_price * item.quantity),
                            "shipping_fee": round(shipping_per_item, 6),
                            "discount": round(discount_per_item, 6),
                            "order_status": order.status,
                        }
                    )
        else:
            # Fallback: one aggregate row per product metric using period start
            # as the date key.
            period_date_key = _date_key(start)
            for m in result.product_metrics:
                rows.append(
                    {
                        "order_id": f"AGG_{m.product.product_id}",
                        "order_date_key": period_date_key,
                        "product_id": m.product.product_id,
                        "quantity": m.units_sold,
                        "unit_price": float(m.product.current_price),
                        "line_total": float(m.revenue_contribution),
                        "shipping_fee": 0.0,
                        "discount": 0.0,
                        "order_status": "COMPLETED",
                    }
                )

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Sales_Fact written: %s (%d rows)", path, len(rows))
        return path

    def _write_product_dim(
        self,
        result: AnalysisResult,
        output_dir: Path,
        shop_id: int,
        start: datetime,
        end: datetime,
    ) -> Path:
        """Write Product_Dim.csv.

        Columns: product_id, product_name, category, current_price,
                 average_rating, total_units_sold
        """
        stem = _file_stem(shop_id, start, end, "Product_Dim")
        path = output_dir / f"{stem}.csv"
        _warn_if_exists(path)

        fieldnames = [
            "product_id",
            "product_name",
            "category",
            "current_price",
            "average_rating",
            "total_units_sold",
        ]

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for m in result.product_metrics:
                p = m.product
                writer.writerow(
                    {
                        "product_id": p.product_id,
                        "product_name": p.name,
                        "category": p.category,
                        "current_price": float(p.current_price),
                        "average_rating": p.average_rating,
                        "total_units_sold": p.total_units_sold,
                    }
                )

        logger.info("Product_Dim written: %s", path)
        return path

    def _write_date_dim(
        self,
        result: AnalysisResult,
        output_dir: Path,
        shop_id: int,
        start: datetime,
        end: datetime,
    ) -> Path:
        """Write Date_Dim.csv covering every calendar date in the period.

        Columns: date_key, full_date, day_of_week, day_name, week_number,
                 fiscal_week, month, month_name, fiscal_month, quarter,
                 fiscal_quarter, year, fiscal_year, is_weekend

        Requirements: 6.1, 6.4
        """
        stem = _file_stem(shop_id, start, end, "Date_Dim")
        path = output_dir / f"{stem}.csv"
        _warn_if_exists(path)

        fieldnames = [
            "date_key",
            "full_date",
            "day_of_week",
            "day_name",
            "week_number",
            "fiscal_week",
            "month",
            "month_name",
            "fiscal_month",
            "quarter",
            "fiscal_quarter",
            "year",
            "fiscal_year",
            "is_weekend",
        ]

        rows = _build_date_dim_rows(start, end)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Date_Dim written: %s (%d rows)", path, len(rows))
        return path

    def _write_recommendation_fact(
        self,
        result: AnalysisResult,
        output_dir: Path,
        shop_id: int,
        start: datetime,
        end: datetime,
    ) -> Path:
        """Write Recommendation_Fact.csv.

        Columns: recommendation_id, category, priority, title, description,
                 product_ids, analysis_date_key
        """
        stem = _file_stem(shop_id, start, end, "Recommendation_Fact")
        path = output_dir / f"{stem}.csv"
        _warn_if_exists(path)

        fieldnames = [
            "recommendation_id",
            "category",
            "priority",
            "title",
            "description",
            "product_ids",
            "analysis_date_key",
        ]

        analysis_date_key = _date_key(datetime.today())

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for idx, rec in enumerate(result.recommendations, start=1):
                writer.writerow(
                    {
                        "recommendation_id": idx,
                        "category": rec.category,
                        "priority": rec.priority,
                        "title": rec.title,
                        "description": rec.description,
                        "product_ids": ",".join(
                            str(pid) for pid in rec.supporting_products
                        ),
                        "analysis_date_key": analysis_date_key,
                    }
                )

        logger.info("Recommendation_Fact written: %s", path)
        return path
