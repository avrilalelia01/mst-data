"""KPI Engine for the Shopee Sales Analyzer.

Provides pure analytical functions over in-memory Order and Product data.
No I/O is performed here — all methods are deterministic given the same inputs.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Literal

from shopee_analyzer.models import (
    Order,
    PeriodBucket,
    Product,
    ProductMetrics,
    SalesPeriod,
    StoreKPIs,
    TrendData,
)


class KPIEngine:
    """Pure-function analytics engine.

    Can be instantiated with no arguments.  All public methods are free of
    side-effects and perform no I/O.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compute_store_kpis(
        self, orders: list[Order], period: SalesPeriod
    ) -> StoreKPIs:
        """Compute aggregate store-level KPIs for a given Sales_Period.

        Requirements: 3.1
        """
        total_revenue = sum(
            (o.total_payment for o in orders), Decimal("0")
        )
        total_orders = len(orders)
        total_units_sold = sum(
            item.quantity for o in orders for item in o.items
        )

        if total_orders > 0:
            average_order_value = total_revenue / Decimal(total_orders)
        else:
            average_order_value = Decimal("0")

        # Gross profit margin: (revenue - shipping - discounts) / revenue
        if total_revenue > 0:
            total_deductions = sum(
                (o.shipping_fee + o.discount_total for o in orders),
                Decimal("0"),
            )
            gross_profit_margin = float(
                (total_revenue - total_deductions) / total_revenue
            )
        else:
            gross_profit_margin = 0.0

        # Return rate: fraction of orders with status RETURN or REFUND
        if total_orders > 0:
            returned = sum(
                1 for o in orders if o.status.upper() in ("RETURN", "REFUND")
            )
            return_rate = returned / total_orders
        else:
            return_rate = 0.0

        trends = self.compute_trends(orders, period)

        return StoreKPIs(
            period=period,
            total_revenue=total_revenue,
            total_orders=total_orders,
            total_units_sold=total_units_sold,
            average_order_value=average_order_value,
            gross_profit_margin=gross_profit_margin,
            return_rate=return_rate,
            trends=trends,
        )

    def compute_product_metrics(
        self, orders: list[Order], products: list[Product]
    ) -> list[ProductMetrics]:
        """Compute per-product metrics from order data and product catalogue.

        Requirements: 3.2
        """
        # Index order items by product_id for O(1) look-ups
        items_by_product: dict[int, list] = defaultdict(list)
        for order in orders:
            for item in order.items:
                items_by_product[item.product_id].append(item)

        result: list[ProductMetrics] = []
        for product in products:
            matching_items = items_by_product.get(product.product_id, [])

            revenue_contribution = sum(
                (item.unit_price * item.quantity for item in matching_items),
                Decimal("0"),
            )
            units_sold = sum(item.quantity for item in matching_items)

            # Profit margin estimate: 60% COGS assumption, clamped to [0, 1]
            if product.current_price > 0:
                estimated_cost = product.current_price * Decimal("0.6")
                raw_margin = float(
                    (product.current_price - estimated_cost)
                    / product.current_price
                )
                profit_margin_per_unit = max(0.0, min(1.0, raw_margin))
            else:
                profit_margin_per_unit = 0.0

            # Conversion rate: units_sold / total_views
            if product.total_views > 0:
                conversion_rate = units_sold / product.total_views
            else:
                conversion_rate = 0.0

            result.append(
                ProductMetrics(
                    product=product,
                    revenue_contribution=revenue_contribution,
                    units_sold=units_sold,
                    profit_margin_per_unit=profit_margin_per_unit,
                    conversion_rate=conversion_rate,
                )
            )

        return result

    def rank_products(
        self,
        metrics: list[ProductMetrics],
        by: Literal["revenue", "units"],
        n: int = 10,
    ) -> list[ProductMetrics]:
        """Return the top-N product metrics sorted by the chosen criterion.

        Requirements: 3.3, 3.4
        """
        if by == "revenue":
            key = lambda m: m.revenue_contribution  # noqa: E731
        else:
            key = lambda m: m.units_sold  # noqa: E731

        sorted_metrics = sorted(metrics, key=key, reverse=True)
        return sorted_metrics[: min(n, len(sorted_metrics))]

    def compute_trends(
        self, orders: list[Order], period: SalesPeriod
    ) -> TrendData:
        """Compute month-over-month and week-over-week revenue trends.

        Bucketing rules:
        - Monthly label: "YYYY-MM"
        - Weekly label:  "YYYY-WXX" (ISO week number, zero-padded to 2 digits)

        When fewer than 2 buckets exist for a granularity, the corresponding
        change value is set to None (Requirement 3.6).

        Requirements: 3.5, 3.6
        """
        monthly_buckets = self._build_monthly_buckets(orders)
        weekly_buckets = self._build_weekly_buckets(orders)

        mom_revenue_change = self._pct_change(monthly_buckets)
        wow_revenue_change = self._pct_change(weekly_buckets)

        return TrendData(
            mom_revenue_change=mom_revenue_change,
            wow_revenue_change=wow_revenue_change,
            monthly_buckets=monthly_buckets,
            weekly_buckets=weekly_buckets,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_monthly_buckets(orders: list[Order]) -> list[PeriodBucket]:
        """Group orders into monthly buckets sorted by start datetime."""
        buckets: dict[str, dict] = {}

        for order in orders:
            label = order.created_at.strftime("%Y-%m")
            if label not in buckets:
                buckets[label] = {
                    "label": label,
                    "start": order.created_at,
                    "end": order.created_at,
                    "revenue": Decimal("0"),
                    "order_count": 0,
                }
            b = buckets[label]
            b["revenue"] += order.total_payment
            b["order_count"] += 1
            if order.created_at < b["start"]:
                b["start"] = order.created_at
            if order.created_at > b["end"]:
                b["end"] = order.created_at

        return sorted(
            [
                PeriodBucket(
                    label=v["label"],
                    start=v["start"],
                    end=v["end"],
                    revenue=v["revenue"],
                    order_count=v["order_count"],
                )
                for v in buckets.values()
            ],
            key=lambda b: b.start,
        )

    @staticmethod
    def _build_weekly_buckets(orders: list[Order]) -> list[PeriodBucket]:
        """Group orders into ISO weekly buckets sorted by start datetime."""
        buckets: dict[str, dict] = {}

        for order in orders:
            iso = order.created_at.isocalendar()
            label = f"{iso.year}-W{iso.week:02d}"
            if label not in buckets:
                buckets[label] = {
                    "label": label,
                    "start": order.created_at,
                    "end": order.created_at,
                    "revenue": Decimal("0"),
                    "order_count": 0,
                }
            b = buckets[label]
            b["revenue"] += order.total_payment
            b["order_count"] += 1
            if order.created_at < b["start"]:
                b["start"] = order.created_at
            if order.created_at > b["end"]:
                b["end"] = order.created_at

        return sorted(
            [
                PeriodBucket(
                    label=v["label"],
                    start=v["start"],
                    end=v["end"],
                    revenue=v["revenue"],
                    order_count=v["order_count"],
                )
                for v in buckets.values()
            ],
            key=lambda b: b.start,
        )

    @staticmethod
    def _pct_change(buckets: list[PeriodBucket]) -> float | None:
        """Return percentage change between the last two buckets, or None.

        Returns None when:
        - Fewer than 2 buckets exist
        - The previous bucket has zero revenue (avoids division by zero)
        """
        if len(buckets) < 2:
            return None
        prev = buckets[-2]
        last = buckets[-1]
        if prev.revenue == 0:
            return None
        return float((last.revenue - prev.revenue) / prev.revenue * 100)
