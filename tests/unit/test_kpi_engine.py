"""Unit and property-based tests for KPIEngine.

Tests cover:
- Property 7: KPI mathematical invariants (Requirements 3.1)
- Property 8: Per-product metric correctness (Requirements 3.2)
- Property 9: Product ranking ordering invariants (Requirements 3.3, 3.4)
- Trend computation edge cases (Requirements 3.5, 3.6)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shopee_analyzer.kpi_engine import KPIEngine
from shopee_analyzer.models import (
    Order,
    OrderItem,
    PeriodBucket,
    Product,
    ProductMetrics,
    SalesPeriod,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_order(
    order_id: str = "ORD1",
    status: str = "COMPLETED",
    total_payment: str = "100.00",
    shipping_fee: str = "5.00",
    discount_total: str = "0.00",
    created_at: datetime | None = None,
    items: list[OrderItem] | None = None,
) -> Order:
    if created_at is None:
        created_at = datetime(2024, 1, 15, tzinfo=timezone.utc)
    if items is None:
        items = [OrderItem(product_id=1, product_name="Prod A", quantity=2, unit_price=Decimal("50.00"))]
    return Order(
        order_id=order_id,
        status=status,
        items=items,
        total_payment=Decimal(total_payment),
        shipping_fee=Decimal(shipping_fee),
        discount_total=Decimal(discount_total),
        created_at=created_at,
    )


def make_product(
    product_id: int = 1,
    name: str = "Prod A",
    current_price: str = "50.00",
    total_views: int = 1000,
    total_units_sold: int = 10,
) -> Product:
    return Product(
        product_id=product_id,
        name=name,
        category="Electronics",
        current_price=Decimal(current_price),
        stock_quantity=100,
        review_count=5,
        average_rating=4.5,
        total_units_sold=total_units_sold,
        total_views=total_views,
    )


DEFAULT_PERIOD = SalesPeriod(
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 1, 31, tzinfo=timezone.utc),
)

ENGINE = KPIEngine()


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

@st.composite
def order_item_strategy(draw):
    quantity = draw(st.integers(min_value=1, max_value=100))
    unit_price = draw(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999.99"), places=2))
    product_id = draw(st.integers(min_value=1, max_value=100))
    return OrderItem(
        product_id=product_id,
        product_name=f"Product {product_id}",
        quantity=quantity,
        unit_price=unit_price,
    )


@st.composite
def order_strategy(draw, fixed_product_id: int | None = None):
    items = draw(st.lists(order_item_strategy(), min_size=1, max_size=5))
    if fixed_product_id is not None:
        # Ensure at least one item has the specified product_id
        items[0] = OrderItem(
            product_id=fixed_product_id,
            product_name=f"Product {fixed_product_id}",
            quantity=items[0].quantity,
            unit_price=items[0].unit_price,
        )
    total_payment = draw(
        st.decimals(min_value=Decimal("0.01"), max_value=Decimal("99999.99"), places=2)
    )
    shipping_fee = draw(
        st.decimals(min_value=Decimal("0.00"), max_value=Decimal("999.99"), places=2)
    )
    discount_total = draw(
        st.decimals(min_value=Decimal("0.00"), max_value=Decimal("999.99"), places=2)
    )
    status = draw(st.sampled_from(["COMPLETED", "CANCELLED", "RETURN", "REFUND", "PENDING"]))
    created_at = draw(
        st.datetimes(
            min_value=datetime(2024, 1, 1),
            max_value=datetime(2024, 12, 31),
        ).map(lambda dt: dt.replace(tzinfo=timezone.utc))
    )
    return Order(
        order_id=draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")))),
        status=status,
        items=items,
        total_payment=total_payment,
        shipping_fee=shipping_fee,
        discount_total=discount_total,
        created_at=created_at,
    )


@st.composite
def product_metrics_strategy(draw):
    revenue = draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("99999.99"), places=2))
    units_sold = draw(st.integers(min_value=0, max_value=10000))
    product = make_product(
        product_id=draw(st.integers(min_value=1, max_value=1000)),
        total_views=draw(st.integers(min_value=0, max_value=100000)),
    )
    return ProductMetrics(
        product=product,
        revenue_contribution=revenue,
        units_sold=units_sold,
        profit_margin_per_unit=0.4,
        conversion_rate=0.01,
    )


# ---------------------------------------------------------------------------
# Tests: compute_store_kpis (Requirements 3.1)
# ---------------------------------------------------------------------------

class TestComputeStoreKpis:
    """Unit tests for compute_store_kpis."""

    def test_empty_orders_returns_zero_kpis(self):
        kpis = ENGINE.compute_store_kpis([], DEFAULT_PERIOD)
        assert kpis.total_revenue == Decimal("0")
        assert kpis.total_orders == 0
        assert kpis.total_units_sold == 0
        assert kpis.average_order_value == Decimal("0")
        assert kpis.gross_profit_margin == 0.0
        assert kpis.return_rate == 0.0

    def test_total_revenue_sums_total_payment(self):
        orders = [make_order("O1", total_payment="100.00"), make_order("O2", total_payment="200.00")]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.total_revenue == Decimal("300.00")

    def test_total_orders_counts_all(self):
        orders = [make_order(f"O{i}") for i in range(5)]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.total_orders == 5

    def test_total_units_sold_sums_all_items(self):
        items = [
            OrderItem(product_id=1, product_name="A", quantity=3, unit_price=Decimal("10")),
            OrderItem(product_id=2, product_name="B", quantity=2, unit_price=Decimal("5")),
        ]
        orders = [make_order("O1", items=items)]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.total_units_sold == 5

    def test_average_order_value_is_revenue_divided_by_orders(self):
        orders = [make_order("O1", total_payment="100.00"), make_order("O2", total_payment="200.00")]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.average_order_value == Decimal("150.00")

    def test_return_rate_counts_return_and_refund(self):
        orders = [
            make_order("O1", status="COMPLETED"),
            make_order("O2", status="RETURN"),
            make_order("O3", status="REFUND"),
            make_order("O4", status="CANCELLED"),
        ]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.return_rate == pytest.approx(0.5)

    def test_return_rate_case_insensitive(self):
        orders = [
            make_order("O1", status="return"),
            make_order("O2", status="Refund"),
            make_order("O3", status="COMPLETED"),
        ]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.return_rate == pytest.approx(2 / 3)

    def test_return_rate_in_range(self):
        orders = [make_order(f"O{i}", status="RETURN") for i in range(10)]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert 0.0 <= kpis.return_rate <= 1.0

    def test_gross_profit_margin_formula(self):
        orders = [
            make_order("O1", total_payment="1000.00", shipping_fee="50.00", discount_total="100.00"),
        ]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        # (1000 - 150) / 1000 = 0.85
        assert kpis.gross_profit_margin == pytest.approx(0.85)

    def test_period_stored_on_result(self):
        kpis = ENGINE.compute_store_kpis([], DEFAULT_PERIOD)
        assert kpis.period is DEFAULT_PERIOD

    def test_trends_attached(self):
        orders = [make_order("O1")]
        kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)
        assert kpis.trends is not None


# ---------------------------------------------------------------------------
# Property 7: KPI mathematical invariants (Requirements 3.1)
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 7: KPI mathematical invariants
@given(st.lists(order_strategy(), min_size=1, max_size=50))
@settings(max_examples=100)
def test_kpi_mathematical_invariants(orders):
    """Validates: Requirements 3.1"""
    kpis = ENGINE.compute_store_kpis(orders, DEFAULT_PERIOD)

    # total_revenue == sum of total_payment
    expected_revenue = sum((o.total_payment for o in orders), Decimal("0"))
    assert kpis.total_revenue == expected_revenue

    # average_order_value == total_revenue / total_orders
    assert kpis.total_orders == len(orders)
    expected_aov = expected_revenue / Decimal(len(orders))
    assert kpis.average_order_value == expected_aov

    # total_units_sold == sum of all item quantities
    expected_units = sum(item.quantity for o in orders for item in o.items)
    assert kpis.total_units_sold == expected_units

    # return_rate in [0.0, 1.0]
    assert 0.0 <= kpis.return_rate <= 1.0


# ---------------------------------------------------------------------------
# Tests: compute_product_metrics (Requirements 3.2)
# ---------------------------------------------------------------------------

class TestComputeProductMetrics:
    """Unit tests for compute_product_metrics."""

    def test_revenue_contribution_sums_unit_price_times_quantity(self):
        items = [OrderItem(product_id=1, product_name="A", quantity=3, unit_price=Decimal("10.00"))]
        orders = [make_order("O1", items=items)]
        product = make_product(product_id=1)
        metrics = ENGINE.compute_product_metrics(orders, [product])
        assert metrics[0].revenue_contribution == Decimal("30.00")

    def test_units_sold_sums_quantities(self):
        items = [OrderItem(product_id=1, product_name="A", quantity=5, unit_price=Decimal("10.00"))]
        orders = [make_order("O1", items=items)]
        product = make_product(product_id=1)
        metrics = ENGINE.compute_product_metrics(orders, [product])
        assert metrics[0].units_sold == 5

    def test_product_not_in_orders_has_zero_revenue_and_units(self):
        orders = [make_order("O1", items=[OrderItem(product_id=99, product_name="X", quantity=1, unit_price=Decimal("1"))])]
        product = make_product(product_id=1)
        metrics = ENGINE.compute_product_metrics(orders, [product])
        assert metrics[0].revenue_contribution == Decimal("0")
        assert metrics[0].units_sold == 0

    def test_conversion_rate_with_views(self):
        items = [OrderItem(product_id=1, product_name="A", quantity=10, unit_price=Decimal("10.00"))]
        orders = [make_order("O1", items=items)]
        product = make_product(product_id=1, total_views=200)
        metrics = ENGINE.compute_product_metrics(orders, [product])
        assert metrics[0].conversion_rate == pytest.approx(10 / 200)

    def test_conversion_rate_zero_views(self):
        items = [OrderItem(product_id=1, product_name="A", quantity=5, unit_price=Decimal("10.00"))]
        orders = [make_order("O1", items=items)]
        product = make_product(product_id=1, total_views=0)
        metrics = ENGINE.compute_product_metrics(orders, [product])
        assert metrics[0].conversion_rate == 0.0

    def test_profit_margin_uses_60_pct_cogs_estimate(self):
        product = make_product(product_id=1, current_price="100.00")
        metrics = ENGINE.compute_product_metrics([], [product])
        # estimated_cost = 100 * 0.6 = 60; margin = (100-60)/100 = 0.4
        assert metrics[0].profit_margin_per_unit == pytest.approx(0.4)

    def test_profit_margin_clamped_to_zero_when_price_is_zero(self):
        product = make_product(product_id=1, current_price="0.00")
        metrics = ENGINE.compute_product_metrics([], [product])
        assert metrics[0].profit_margin_per_unit == 0.0

    def test_returns_one_metric_per_product(self):
        products = [make_product(product_id=i) for i in range(1, 6)]
        metrics = ENGINE.compute_product_metrics([], products)
        assert len(metrics) == 5

    def test_multiple_orders_same_product_aggregated(self):
        items = [OrderItem(product_id=1, product_name="A", quantity=2, unit_price=Decimal("10.00"))]
        orders = [make_order(f"O{i}", items=items) for i in range(3)]
        product = make_product(product_id=1)
        metrics = ENGINE.compute_product_metrics(orders, [product])
        assert metrics[0].revenue_contribution == Decimal("60.00")
        assert metrics[0].units_sold == 6


# ---------------------------------------------------------------------------
# Property 8: Per-product metric correctness (Requirements 3.2)
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 8: Per-product metric correctness
@given(st.lists(order_strategy(), min_size=0, max_size=30))
@settings(max_examples=100)
def test_product_metric_correctness(orders):
    """Validates: Requirements 3.2"""
    product = make_product(product_id=1, total_views=1000)
    metrics = ENGINE.compute_product_metrics(orders, [product])
    m = metrics[0]

    # revenue_contribution == sum(unit_price * quantity) for matching items
    expected_rev = sum(
        (item.unit_price * item.quantity
         for o in orders
         for item in o.items
         if item.product_id == 1),
        Decimal("0"),
    )
    assert m.revenue_contribution == expected_rev

    # conversion_rate = units_sold / total_views
    expected_units = sum(
        item.quantity
        for o in orders
        for item in o.items
        if item.product_id == 1
    )
    assert m.units_sold == expected_units
    assert m.conversion_rate == pytest.approx(expected_units / 1000)


# ---------------------------------------------------------------------------
# Tests: rank_products (Requirements 3.3, 3.4)
# ---------------------------------------------------------------------------

class TestRankProducts:
    """Unit tests for rank_products."""

    def _make_metrics(self, revenues: list[float], units: list[int]) -> list[ProductMetrics]:
        result = []
        for i, (rev, u) in enumerate(zip(revenues, units)):
            p = make_product(product_id=i + 1)
            result.append(ProductMetrics(
                product=p,
                revenue_contribution=Decimal(str(rev)),
                units_sold=u,
                profit_margin_per_unit=0.4,
                conversion_rate=0.01,
            ))
        return result

    def test_rank_by_revenue_descending(self):
        metrics = self._make_metrics([100, 300, 200], [1, 1, 1])
        ranked = ENGINE.rank_products(metrics, by="revenue", n=3)
        assert [m.revenue_contribution for m in ranked] == [
            Decimal("300"), Decimal("200"), Decimal("100")
        ]

    def test_rank_by_units_descending(self):
        metrics = self._make_metrics([1, 1, 1], [5, 15, 10])
        ranked = ENGINE.rank_products(metrics, by="units", n=3)
        assert [m.units_sold for m in ranked] == [15, 10, 5]

    def test_n_limits_result_length(self):
        metrics = self._make_metrics([100, 200, 300, 400, 500], [1, 2, 3, 4, 5])
        ranked = ENGINE.rank_products(metrics, by="revenue", n=3)
        assert len(ranked) == 3

    def test_n_larger_than_list_returns_all(self):
        metrics = self._make_metrics([100, 200], [1, 2])
        ranked = ENGINE.rank_products(metrics, by="revenue", n=10)
        assert len(ranked) == 2

    def test_empty_list_returns_empty(self):
        ranked = ENGINE.rank_products([], by="revenue", n=10)
        assert ranked == []

    def test_default_n_is_10(self):
        metrics = self._make_metrics([float(i) for i in range(1, 16)], [i for i in range(1, 16)])
        ranked = ENGINE.rank_products(metrics, by="revenue")
        assert len(ranked) == 10


# ---------------------------------------------------------------------------
# Property 9: Product ranking ordering invariants (Requirements 3.3, 3.4)
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 9: Product ranking ordering invariants
@given(
    st.lists(product_metrics_strategy(), min_size=0, max_size=50),
    st.sampled_from(["revenue", "units"]),
    st.integers(min_value=1, max_value=20),
)
@settings(max_examples=100)
def test_product_ranking_invariants(metrics, by, n):
    """Validates: Requirements 3.3, 3.4"""
    ranked = ENGINE.rank_products(metrics, by=by, n=n)

    # Result length is min(n, len(metrics))
    assert len(ranked) == min(n, len(metrics))

    if len(ranked) < 2:
        return

    # Sorted descending by criterion
    if by == "revenue":
        values = [m.revenue_contribution for m in ranked]
    else:
        values = [m.units_sold for m in ranked]

    for i in range(len(values) - 1):
        assert values[i] >= values[i + 1], (
            f"Ranking not descending at index {i}: {values[i]} < {values[i+1]}"
        )

    # Every included product has criterion >= every excluded product's criterion
    if len(ranked) < len(metrics):
        excluded = [m for m in metrics if m not in ranked]
        if excluded:
            if by == "revenue":
                min_included = min(m.revenue_contribution for m in ranked)
                max_excluded = max(m.revenue_contribution for m in excluded)
            else:
                min_included = min(m.units_sold for m in ranked)
                max_excluded = max(m.units_sold for m in excluded)
            assert min_included >= max_excluded


# ---------------------------------------------------------------------------
# Tests: compute_trends (Requirements 3.5, 3.6)
# ---------------------------------------------------------------------------

class TestComputeTrends:
    """Unit tests for compute_trends."""

    def test_empty_orders_returns_none_changes(self):
        trends = ENGINE.compute_trends([], DEFAULT_PERIOD)
        assert trends.mom_revenue_change is None
        assert trends.wow_revenue_change is None
        assert trends.monthly_buckets == []
        assert trends.weekly_buckets == []

    def test_single_month_returns_none_mom(self):
        orders = [make_order("O1", created_at=datetime(2024, 1, 15, tzinfo=timezone.utc))]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        assert trends.mom_revenue_change is None

    def test_single_week_returns_none_wow(self):
        orders = [make_order("O1", created_at=datetime(2024, 1, 15, tzinfo=timezone.utc))]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        assert trends.wow_revenue_change is None

    def test_two_months_computes_mom(self):
        orders = [
            make_order("O1", total_payment="100.00", created_at=datetime(2024, 1, 15, tzinfo=timezone.utc)),
            make_order("O2", total_payment="150.00", created_at=datetime(2024, 2, 15, tzinfo=timezone.utc)),
        ]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        # (150 - 100) / 100 * 100 = 50.0%
        assert trends.mom_revenue_change == pytest.approx(50.0)

    def test_two_weeks_computes_wow(self):
        orders = [
            make_order("O1", total_payment="200.00", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            make_order("O2", total_payment="100.00", created_at=datetime(2024, 1, 8, tzinfo=timezone.utc)),
        ]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        # (100 - 200) / 200 * 100 = -50.0%
        assert trends.wow_revenue_change == pytest.approx(-50.0)

    def test_monthly_buckets_sorted_by_start(self):
        orders = [
            make_order("O1", total_payment="100.00", created_at=datetime(2024, 3, 1, tzinfo=timezone.utc)),
            make_order("O2", total_payment="200.00", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            make_order("O3", total_payment="150.00", created_at=datetime(2024, 2, 1, tzinfo=timezone.utc)),
        ]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        labels = [b.label for b in trends.monthly_buckets]
        assert labels == ["2024-01", "2024-02", "2024-03"]

    def test_monthly_bucket_label_format(self):
        orders = [make_order("O1", created_at=datetime(2024, 6, 15, tzinfo=timezone.utc))]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        assert trends.monthly_buckets[0].label == "2024-06"

    def test_weekly_bucket_label_format(self):
        # 2024-01-15 is ISO week 3
        orders = [make_order("O1", created_at=datetime(2024, 1, 15, tzinfo=timezone.utc))]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        assert trends.weekly_buckets[0].label == "2024-W03"

    def test_mom_none_when_prev_revenue_is_zero(self):
        """Guard against division by zero in mom computation."""
        orders = [
            make_order("O1", total_payment="0.00", created_at=datetime(2024, 1, 15, tzinfo=timezone.utc)),
            make_order("O2", total_payment="100.00", created_at=datetime(2024, 2, 15, tzinfo=timezone.utc)),
        ]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        assert trends.mom_revenue_change is None

    def test_bucket_revenue_aggregated_correctly(self):
        orders = [
            make_order("O1", total_payment="100.00", created_at=datetime(2024, 1, 5, tzinfo=timezone.utc)),
            make_order("O2", total_payment="200.00", created_at=datetime(2024, 1, 20, tzinfo=timezone.utc)),
        ]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        assert len(trends.monthly_buckets) == 1
        assert trends.monthly_buckets[0].revenue == Decimal("300.00")

    def test_bucket_order_count_correct(self):
        orders = [
            make_order("O1", created_at=datetime(2024, 1, 5, tzinfo=timezone.utc)),
            make_order("O2", created_at=datetime(2024, 1, 20, tzinfo=timezone.utc)),
            make_order("O3", created_at=datetime(2024, 2, 10, tzinfo=timezone.utc)),
        ]
        trends = ENGINE.compute_trends(orders, DEFAULT_PERIOD)
        jan_bucket = next(b for b in trends.monthly_buckets if b.label == "2024-01")
        assert jan_bucket.order_count == 2
