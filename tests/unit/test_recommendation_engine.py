"""Unit and property-based tests for RecommendationEngine.

Tests cover:
- Property 11: Recommendation trigger rules (Requirements 4.4, 4.5, 4.6, 4.7)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shopee_analyzer.models import (
    PeriodBucket,
    Product,
    ProductMetrics,
    Recommendation,
    SalesPeriod,
    StoreKPIs,
    TrendData,
)
from shopee_analyzer.recommendation_engine import RecommendationEngine


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ENGINE = RecommendationEngine()

DEFAULT_PERIOD = SalesPeriod(
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 1, 31, tzinfo=timezone.utc),
)

_EMPTY_TREND = TrendData(
    mom_revenue_change=None,
    wow_revenue_change=None,
    monthly_buckets=[],
    weekly_buckets=[],
)

_MINIMAL_KPIS = StoreKPIs(
    period=DEFAULT_PERIOD,
    total_revenue=Decimal("0"),
    total_orders=0,
    total_units_sold=0,
    average_order_value=Decimal("0"),
    gross_profit_margin=0.0,
    return_rate=0.0,
    trends=_EMPTY_TREND,
)


def make_product(
    product_id: int = 1,
    name: str = "Test Product",
    current_price: str = "100.00",
    stock_quantity: int = 50,
    review_count: int = 0,
    average_rating: float = 4.5,
    total_units_sold: int = 0,
    total_views: int = 0,
    category: str = "Electronics",
) -> Product:
    return Product(
        product_id=product_id,
        name=name,
        category=category,
        current_price=Decimal(current_price),
        stock_quantity=stock_quantity,
        review_count=review_count,
        average_rating=average_rating,
        total_units_sold=total_units_sold,
        total_views=total_views,
    )


def make_metrics(
    product: Product | None = None,
    profit_margin_per_unit: float = 0.4,
    conversion_rate: float = 0.05,
    revenue_contribution: str = "1000.00",
    units_sold: int = 10,
) -> ProductMetrics:
    if product is None:
        product = make_product()
    return ProductMetrics(
        product=product,
        revenue_contribution=Decimal(revenue_contribution),
        units_sold=units_sold,
        profit_margin_per_unit=profit_margin_per_unit,
        conversion_rate=conversion_rate,
    )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

@st.composite
def triggering_product_strategy(draw, trigger: str):
    """Build a (ProductMetrics, expected_priority) pair that satisfies exactly
    the given trigger condition.

    Supported triggers:
      "low_margin"      → profit_margin_per_unit < 0.10  → HIGH priority
      "out_of_stock"    → stock_quantity == 0, total_units_sold > 0  → HIGH priority
      "low_rating"      → average_rating < 3.5, review_count > 10   → MEDIUM priority
      "low_conversion"  → conversion_rate < 0.02, total_views > 100  → MEDIUM priority
    """
    product_id = draw(st.integers(min_value=1, max_value=100000))
    name = draw(st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")),
    ))

    if trigger == "low_margin":
        # profit_margin_per_unit drawn from [0.0, 0.099]
        margin = draw(st.floats(min_value=0.0, max_value=0.099, allow_nan=False, allow_infinity=False))
        # Use generous safe values for other fields to avoid other rules firing
        product = make_product(
            product_id=product_id,
            name=name,
            stock_quantity=draw(st.integers(min_value=1, max_value=10000)),  # not out-of-stock
            total_units_sold=draw(st.integers(min_value=0, max_value=10000)),
            average_rating=draw(st.floats(min_value=3.5, max_value=5.0, allow_nan=False)),  # not low-rating
            review_count=draw(st.integers(min_value=0, max_value=10)),          # review_count <= 10 prevents low_rating
            total_views=draw(st.integers(min_value=0, max_value=100)),           # total_views <= 100 prevents low_conversion
        )
        conversion_rate = draw(st.floats(min_value=0.02, max_value=1.0, allow_nan=False))  # not low-conversion
        metrics = make_metrics(
            product=product,
            profit_margin_per_unit=margin,
            conversion_rate=conversion_rate,
        )
        return metrics, "HIGH"

    elif trigger == "out_of_stock":
        # stock_quantity == 0, total_units_sold >= 1
        units_sold_on_product = draw(st.integers(min_value=1, max_value=10000))
        product = make_product(
            product_id=product_id,
            name=name,
            stock_quantity=0,
            total_units_sold=units_sold_on_product,
            average_rating=draw(st.floats(min_value=3.5, max_value=5.0, allow_nan=False)),  # not low-rating
            review_count=draw(st.integers(min_value=0, max_value=10)),          # review_count <= 10 prevents low_rating
            total_views=draw(st.integers(min_value=0, max_value=100)),           # total_views <= 100 prevents low_conversion
        )
        metrics = make_metrics(
            product=product,
            profit_margin_per_unit=draw(st.floats(min_value=0.10, max_value=1.0, allow_nan=False)),  # not low-margin
            conversion_rate=draw(st.floats(min_value=0.02, max_value=1.0, allow_nan=False)),          # not low-conversion
        )
        return metrics, "HIGH"

    elif trigger == "low_rating":
        # average_rating < 3.5 and review_count > 10
        rating = draw(st.floats(min_value=0.0, max_value=3.49, allow_nan=False, allow_infinity=False))
        review_count = draw(st.integers(min_value=11, max_value=10000))
        product = make_product(
            product_id=product_id,
            name=name,
            stock_quantity=draw(st.integers(min_value=1, max_value=10000)),  # not out-of-stock
            total_units_sold=draw(st.integers(min_value=0, max_value=10000)),
            average_rating=rating,
            review_count=review_count,
            total_views=draw(st.integers(min_value=0, max_value=100)),   # total_views <= 100 prevents low_conversion
        )
        metrics = make_metrics(
            product=product,
            profit_margin_per_unit=draw(st.floats(min_value=0.10, max_value=1.0, allow_nan=False)),  # not low-margin
            conversion_rate=draw(st.floats(min_value=0.02, max_value=1.0, allow_nan=False)),          # not low-conversion
        )
        return metrics, "MEDIUM"

    elif trigger == "low_conversion":
        # conversion_rate < 0.02 and total_views > 100
        conversion = draw(st.floats(min_value=0.0, max_value=0.0199, allow_nan=False, allow_infinity=False))
        total_views = draw(st.integers(min_value=101, max_value=10000))
        product = make_product(
            product_id=product_id,
            name=name,
            stock_quantity=draw(st.integers(min_value=1, max_value=10000)),  # not out-of-stock
            total_units_sold=draw(st.integers(min_value=0, max_value=10000)),
            average_rating=draw(st.floats(min_value=3.5, max_value=5.0, allow_nan=False)),  # not low-rating
            review_count=draw(st.integers(min_value=0, max_value=10)),          # review_count <= 10 prevents low_rating
            total_views=total_views,
        )
        metrics = make_metrics(
            product=product,
            profit_margin_per_unit=draw(st.floats(min_value=0.10, max_value=1.0, allow_nan=False)),  # not low-margin
            conversion_rate=conversion,
        )
        return metrics, "MEDIUM"

    else:
        raise ValueError(f"Unknown trigger: {trigger}")


# ---------------------------------------------------------------------------
# Property 11: Recommendation trigger rules (Requirements 4.4, 4.5, 4.6, 4.7)
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 11: Recommendation trigger rules
@given(
    st.one_of(
        triggering_product_strategy("low_margin"),
        triggering_product_strategy("out_of_stock"),
        triggering_product_strategy("low_rating"),
        triggering_product_strategy("low_conversion"),
    )
)
@settings(max_examples=100)
def test_recommendation_trigger_rules(triggering_metrics_and_expected):
    """Validates: Requirements 4.4, 4.5, 4.6, 4.7

    For each trigger condition, a dataset satisfying exactly that condition is
    generated; the engine must produce at least one recommendation with the
    expected priority for the triggering product.
    """
    metrics, expected_priority = triggering_metrics_and_expected
    product_id = metrics.product.product_id

    recommendations = ENGINE.generate(_MINIMAL_KPIS, [metrics])

    # At least one recommendation must exist for this product
    product_recs = [
        r for r in recommendations
        if product_id in r.supporting_products
    ]
    assert len(product_recs) >= 1, (
        f"Expected at least one recommendation for product {product_id} "
        f"with metrics {metrics}, but got none. "
        f"All recommendations: {recommendations}"
    )

    # At least one recommendation for this product must have the expected priority
    priorities = [r.priority for r in product_recs]
    assert expected_priority in priorities, (
        f"Expected a '{expected_priority}' recommendation for product {product_id}, "
        f"but got priorities: {priorities}. Metrics: {metrics}"
    )


# ---------------------------------------------------------------------------
# Unit tests for individual trigger rules (Requirements 4.4, 4.5, 4.6, 4.7)
# ---------------------------------------------------------------------------

class TestLowMarginRule:
    """Unit tests for the low-margin trigger (Requirement 4.4)."""

    def test_fires_when_margin_below_threshold(self):
        product = make_product()
        m = make_metrics(product=product, profit_margin_per_unit=0.05)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        product_recs = [r for r in recs if product.product_id in r.supporting_products]
        high_pricing = [r for r in product_recs if r.priority == "HIGH" and r.category == "pricing"]
        assert len(high_pricing) == 1

    def test_does_not_fire_when_margin_at_threshold(self):
        product = make_product()
        m = make_metrics(product=product, profit_margin_per_unit=0.10)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        high_pricing = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "HIGH"
            and r.category == "pricing"
        ]
        assert len(high_pricing) == 0

    def test_does_not_fire_when_margin_above_threshold(self):
        product = make_product()
        m = make_metrics(product=product, profit_margin_per_unit=0.50)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        high_pricing = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "HIGH"
            and r.category == "pricing"
        ]
        assert len(high_pricing) == 0

    def test_fires_at_zero_margin(self):
        product = make_product()
        m = make_metrics(product=product, profit_margin_per_unit=0.0)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        high_pricing = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "HIGH"
            and r.category == "pricing"
        ]
        assert len(high_pricing) == 1


class TestOutOfStockRule:
    """Unit tests for the out-of-stock trigger (Requirement 4.5)."""

    def test_fires_when_stock_zero_and_units_sold_positive(self):
        product = make_product(stock_quantity=0, total_units_sold=10)
        m = make_metrics(product=product)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        inv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "HIGH"
            and r.category == "inventory"
        ]
        assert len(inv_recs) == 1

    def test_does_not_fire_when_stock_is_positive(self):
        product = make_product(stock_quantity=5, total_units_sold=10)
        m = make_metrics(product=product)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        inv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.category == "inventory"
        ]
        assert len(inv_recs) == 0

    def test_does_not_fire_when_stock_zero_but_no_sales(self):
        product = make_product(stock_quantity=0, total_units_sold=0)
        m = make_metrics(product=product)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        inv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.category == "inventory"
        ]
        assert len(inv_recs) == 0


class TestLowRatingRule:
    """Unit tests for the low-rating trigger (Requirement 4.6)."""

    def test_fires_when_rating_below_3_5_and_review_count_above_10(self):
        product = make_product(average_rating=3.0, review_count=15)
        m = make_metrics(product=product)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        listing_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
        ]
        assert len(listing_recs) >= 1

    def test_does_not_fire_when_rating_at_threshold(self):
        product = make_product(
            average_rating=3.5,
            review_count=15,
            total_views=0,   # avoid low_conversion
        )
        m = make_metrics(product=product, conversion_rate=0.05)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        low_rating_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "rating" in r.title.lower()
        ]
        assert len(low_rating_recs) == 0

    def test_does_not_fire_when_review_count_too_low(self):
        product = make_product(
            average_rating=2.0,
            review_count=5,
            total_views=0,
        )
        m = make_metrics(product=product, conversion_rate=0.05)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        low_rating_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "rating" in r.title.lower()
        ]
        assert len(low_rating_recs) == 0

    def test_does_not_fire_when_review_count_exactly_10(self):
        product = make_product(
            average_rating=2.0,
            review_count=10,
            total_views=0,
        )
        m = make_metrics(product=product, conversion_rate=0.05)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        low_rating_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "rating" in r.title.lower()
        ]
        assert len(low_rating_recs) == 0


class TestLowConversionRule:
    """Unit tests for the low-conversion trigger (Requirement 4.7)."""

    def test_fires_when_conversion_below_0_02_and_views_above_100(self):
        product = make_product(total_views=500)
        m = make_metrics(product=product, conversion_rate=0.01)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        conv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "conversion" in r.title.lower()
        ]
        assert len(conv_recs) == 1

    def test_does_not_fire_when_conversion_at_threshold(self):
        product = make_product(
            total_views=500,
            average_rating=4.5,
            review_count=0,
        )
        m = make_metrics(product=product, conversion_rate=0.02)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        conv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "conversion" in r.title.lower()
        ]
        assert len(conv_recs) == 0

    def test_does_not_fire_when_views_at_or_below_100(self):
        product = make_product(total_views=100)
        m = make_metrics(product=product, conversion_rate=0.01)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        conv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "conversion" in r.title.lower()
        ]
        assert len(conv_recs) == 0

    def test_does_not_fire_when_conversion_above_threshold(self):
        product = make_product(total_views=500)
        m = make_metrics(product=product, conversion_rate=0.05)
        recs = ENGINE.generate(_MINIMAL_KPIS, [m])
        conv_recs = [
            r for r in recs
            if product.product_id in r.supporting_products
            and r.priority == "MEDIUM"
            and r.category == "listing"
            and "conversion" in r.title.lower()
        ]
        assert len(conv_recs) == 0
