"""Data models for the Shopee Sales Analyzer.

All domain objects are plain Python dataclasses with no I/O dependencies,
keeping the analytics layer fully testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Configuration:
    """Validated representation of the user-supplied configuration file.

    Requirements: 8.1, 8.2, 8.4
    Note: partner_key must NEVER be written to log output.
    """

    partner_id: int
    partner_key: str          # stored as string; never logged
    shop_id: int
    data_store_path: str      # e.g. "./data/shopee.db"
    output_dir: str           # e.g. "./output"
    refresh_schedule: Literal["hourly", "daily", "weekly"]
    default_period_days: int  # default Sales_Period window in days
    log_level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    log_file: str = "analyzer.log"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@dataclass
class TokenPair:
    """OAuth 2.0 access/refresh token pair for a single shop.

    Requirements: 1.2, 1.3
    """

    access_token: str
    refresh_token: str
    expires_at: datetime      # computed as now() + expire_in seconds
    shop_id: int


# ---------------------------------------------------------------------------
# Time / Period helpers
# ---------------------------------------------------------------------------

@dataclass
class SalesPeriod:
    """Inclusive date range used to scope data collection and analysis.

    Requirements: 2.1, 3.1
    """

    start: datetime
    end: datetime


@dataclass
class PeriodBucket:
    """Aggregate revenue and order count for a single time bucket (week/month).

    Requirements: 3.5
    """

    label: str        # e.g. "2024-W01" or "2024-01"
    start: datetime
    end: datetime
    revenue: Decimal
    order_count: int


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@dataclass
class OrderItem:
    """A single line item within an order.

    Requirements: 2.2
    """

    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal


@dataclass
class Order:
    """A Shopee order with its constituent line items.

    Requirements: 2.2
    """

    order_id: str
    status: str
    items: list[OrderItem]
    total_payment: Decimal
    shipping_fee: Decimal
    discount_total: Decimal
    created_at: datetime


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@dataclass
class Product:
    """A Shopee product listing with its key metadata and sales figures.

    Requirements: 2.3
    """

    product_id: int
    name: str
    category: str
    current_price: Decimal
    stock_quantity: int
    review_count: int
    average_rating: float
    total_units_sold: int
    total_views: int          # from shop analytics endpoint


# ---------------------------------------------------------------------------
# Analysis outputs
# ---------------------------------------------------------------------------

@dataclass
class ProductMetrics:
    """Computed per-product metrics derived from orders and product data.

    Requirements: 3.2
    """

    product: Product
    revenue_contribution: Decimal   # sum of order line totals for this product
    units_sold: int
    profit_margin_per_unit: float   # (unit_price - cost) / unit_price
    conversion_rate: float          # units_sold / total_views (0.0 when views == 0)


@dataclass
class TrendData:
    """Revenue trend data across monthly and weekly buckets.

    Requirements: 3.5, 3.6
    mom_revenue_change / wow_revenue_change are None when < 2 periods exist.
    """

    mom_revenue_change: float | None    # month-over-month % change
    wow_revenue_change: float | None    # week-over-week % change
    monthly_buckets: list[PeriodBucket]
    weekly_buckets: list[PeriodBucket]


@dataclass
class StoreKPIs:
    """Aggregated store-level KPIs for a given Sales_Period.

    Requirements: 3.1
    """

    period: SalesPeriod
    total_revenue: Decimal
    total_orders: int
    total_units_sold: int
    average_order_value: Decimal
    gross_profit_margin: float
    return_rate: float
    trends: TrendData


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """A single actionable recommendation with priority and supporting data.

    Requirements: 4.1, 4.2, 4.3
    """

    category: Literal["pricing", "inventory", "promotional", "listing"]
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    title: str
    description: str
    supporting_products: list[int]      # product_ids
    supporting_metrics: dict[str, Any]  # metric name -> value that triggered this
    estimated_impact: str               # human-readable impact statement


# ---------------------------------------------------------------------------
# Collection and pipeline results
# ---------------------------------------------------------------------------

@dataclass
class CollectionResult:
    """Summary returned by a Data Collector run.

    Requirements: 2.6
    """

    records_collected: int
    records_failed: int
    failed_ids: list[str]
    duration_seconds: float


@dataclass
class AnalysisResult:
    """Full output of one analysis pipeline run.

    Requirements: 3.1, 3.2, 4.1
    """

    shop_id: int
    period: SalesPeriod
    kpis: StoreKPIs
    product_metrics: list[ProductMetrics]
    recommendations: list[Recommendation]


# ---------------------------------------------------------------------------
# Scheduling / history
# ---------------------------------------------------------------------------

@dataclass
class RefreshHistoryEntry:
    """Record of a single scheduled refresh run.

    Requirements: 7.4
    """

    run_id: str           # UUID
    run_timestamp: datetime
    status: Literal["SUCCESS", "FAILURE"]
    records_collected: int
    duration_seconds: float
    failure_reason: str | None
