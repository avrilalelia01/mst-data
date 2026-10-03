"""Recommendation Engine for the Shopee Sales Analyzer.

Applies trigger rules to KPIs and per-product metrics to produce a
prioritised, actionable recommendation list.  All methods are pure
functions — no I/O, no side effects.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from shopee_analyzer.models import (
    ProductMetrics,
    Recommendation,
    StoreKPIs,
)

# Priority sort key: lower value → higher priority when sorted ascending.
_PRIORITY_ORDER: dict[str, int] = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Maximum number of recommendations returned per analysis run.
_MAX_RECOMMENDATIONS = 20


class RecommendationEngine:
    """Pure-function recommendation engine.

    Can be instantiated with no arguments.  All public methods are free
    of side-effects and perform no I/O.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(
        self,
        kpis: StoreKPIs,
        product_metrics: list[ProductMetrics],
    ) -> list[Recommendation]:
        """Apply all six rule functions and return a trimmed, sorted list.

        Steps:
        1. Run every rule against every product and collect non-None results.
        2. Sort by priority: HIGH first, MEDIUM second, LOW last.
        3. Trim to at most 20 recommendations.
        4. Return whatever is available (even < 3 if few triggers fire).

        Requirements: 4.1, 4.8
        """
        recommendations: list[Recommendation] = []

        for metrics in product_metrics:
            rec = self._low_margin_rule(metrics)
            if rec is not None:
                recommendations.append(rec)

            rec = self._out_of_stock_rule(metrics)
            if rec is not None:
                recommendations.append(rec)

            rec = self._low_rating_rule(metrics)
            if rec is not None:
                recommendations.append(rec)

            rec = self._low_conversion_rule(metrics)
            if rec is not None:
                recommendations.append(rec)

        # Pricing-opportunity rule needs the full list to determine top 20%.
        pricing_recs = self._pricing_opportunity_rules(product_metrics)
        recommendations.extend(pricing_recs)

        # Promotional rule operates per-product independently.
        for metrics in product_metrics:
            rec = self._promotional_rule(metrics)
            if rec is not None:
                recommendations.append(rec)

        # Sort: HIGH (0) → MEDIUM (1) → LOW (2).
        recommendations.sort(key=lambda r: _PRIORITY_ORDER[r.priority])

        # Trim to maximum 20.
        return recommendations[:_MAX_RECOMMENDATIONS]

    # ------------------------------------------------------------------
    # Rule functions (one per trigger condition)
    # ------------------------------------------------------------------

    @staticmethod
    def _low_margin_rule(metrics: ProductMetrics) -> Optional[Recommendation]:
        """HIGH-priority pricing recommendation when profit margin < 10%.

        Requirements: 4.4
        """
        if metrics.profit_margin_per_unit >= 0.10:
            return None

        product = metrics.product
        margin_pct = round(metrics.profit_margin_per_unit * 100, 2)

        return Recommendation(
            category="pricing",
            priority="HIGH",
            title=f"Low profit margin on '{product.name}'",
            description=(
                f"'{product.name}' has a profit margin of {margin_pct}%, "
                "which is below the 10% threshold. Consider raising the "
                "selling price or reducing procurement costs to improve "
                "profitability."
            ),
            supporting_products=[product.product_id],
            supporting_metrics={
                "profit_margin_per_unit": metrics.profit_margin_per_unit,
            },
            estimated_impact=(
                f"Raising margin to 10% could add approximately "
                f"{round((0.10 - metrics.profit_margin_per_unit) * float(product.current_price), 2)}"
                f" per unit to profit."
            ),
        )

    @staticmethod
    def _out_of_stock_rule(metrics: ProductMetrics) -> Optional[Recommendation]:
        """HIGH-priority inventory recommendation when stock is 0 and the
        product has recorded sales (indicating demand).

        Requirements: 4.5
        """
        product = metrics.product
        if product.stock_quantity != 0:
            return None
        if product.total_units_sold == 0:
            return None

        return Recommendation(
            category="inventory",
            priority="HIGH",
            title=f"Out of stock: '{product.name}'",
            description=(
                f"'{product.name}' is currently out of stock but has "
                f"{product.total_units_sold} total units sold, indicating "
                "active demand. Restocking promptly will prevent missed sales."
            ),
            supporting_products=[product.product_id],
            supporting_metrics={
                "stock_quantity": product.stock_quantity,
                "total_units_sold": product.total_units_sold,
            },
            estimated_impact=(
                f"Restocking '{product.name}' can immediately recover "
                "sales revenue that is currently being lost."
            ),
        )

    @staticmethod
    def _low_rating_rule(metrics: ProductMetrics) -> Optional[Recommendation]:
        """MEDIUM-priority listing recommendation when rating < 3.5 and
        review count > 10.

        Requirements: 4.6
        """
        product = metrics.product
        if product.average_rating >= 3.5:
            return None
        if product.review_count <= 10:
            return None

        return Recommendation(
            category="listing",
            priority="MEDIUM",
            title=f"Low rating on '{product.name}'",
            description=(
                f"'{product.name}' has an average rating of "
                f"{round(product.average_rating, 2)} stars across "
                f"{product.review_count} reviews, which is below the 3.5 "
                "threshold. Review customer feedback and update the listing "
                "description, images, or packaging to address recurring "
                "complaints."
            ),
            supporting_products=[product.product_id],
            supporting_metrics={
                "average_rating": product.average_rating,
                "review_count": product.review_count,
            },
            estimated_impact=(
                "Improving the rating to 3.5+ can significantly increase "
                "click-through and conversion rates, driving higher revenue."
            ),
        )

    @staticmethod
    def _low_conversion_rule(
        metrics: ProductMetrics,
    ) -> Optional[Recommendation]:
        """MEDIUM-priority listing recommendation when conversion rate < 2%
        and the product has received more than 100 views.

        Requirements: 4.7
        """
        product = metrics.product
        if metrics.conversion_rate >= 0.02:
            return None
        if product.total_views <= 100:
            return None

        conversion_pct = round(metrics.conversion_rate * 100, 2)

        return Recommendation(
            category="listing",
            priority="MEDIUM",
            title=f"Low conversion rate on '{product.name}'",
            description=(
                f"'{product.name}' has a conversion rate of {conversion_pct}% "
                f"({product.total_views} views), which is below the 2% "
                "threshold. Improving the main image, title keywords, or "
                "pricing may convert more visitors into buyers."
            ),
            supporting_products=[product.product_id],
            supporting_metrics={
                "conversion_rate": metrics.conversion_rate,
                "total_views": product.total_views,
            },
            estimated_impact=(
                f"Raising conversion to 2% across {product.total_views} "
                "views could substantially increase units sold."
            ),
        )

    @staticmethod
    def _pricing_opportunity_rules(
        product_metrics: list[ProductMetrics],
    ) -> list[Recommendation]:
        """MEDIUM-priority pricing recommendations for top-revenue products
        (top 20%) that also have high margins (> 0.5).

        The top-20% threshold is computed from the full product list, so
        this rule must receive all metrics at once.

        Requirements: 4.1, 4.2 (pricing category)
        """
        if not product_metrics:
            return []

        # Determine the revenue threshold for the top 20%.
        revenues = sorted(
            (m.revenue_contribution for m in product_metrics), reverse=True
        )
        top_20_index = max(1, round(len(revenues) * 0.20))
        threshold = revenues[top_20_index - 1]  # inclusive cutoff

        recommendations: list[Recommendation] = []

        for metrics in product_metrics:
            if metrics.revenue_contribution < threshold:
                continue
            if metrics.profit_margin_per_unit <= 0.5:
                continue

            product = metrics.product
            margin_pct = round(metrics.profit_margin_per_unit * 100, 1)

            recommendations.append(
                Recommendation(
                    category="pricing",
                    priority="MEDIUM",
                    title=f"Pricing opportunity for '{product.name}'",
                    description=(
                        f"'{product.name}' is a top-revenue product with a "
                        f"{margin_pct}% profit margin. There may be room to "
                        "test a modest price increase, as strong margins "
                        "suggest the product is priced below its perceived "
                        "value ceiling."
                    ),
                    supporting_products=[product.product_id],
                    supporting_metrics={
                        "revenue_contribution": float(
                            metrics.revenue_contribution
                        ),
                        "profit_margin_per_unit": metrics.profit_margin_per_unit,
                    },
                    estimated_impact=(
                        "A small price increase on a high-revenue, "
                        "high-margin product can meaningfully improve "
                        "total gross profit without significant volume loss."
                    ),
                )
            )

        return recommendations

    @staticmethod
    def _promotional_rule(metrics: ProductMetrics) -> Optional[Recommendation]:
        """LOW-priority promotional recommendation for products with low
        traffic (total_views < 100) and good margins (>= 0.3).

        Requirements: 4.1 (promotional category)
        """
        product = metrics.product
        if product.total_views >= 100:
            return None
        if metrics.profit_margin_per_unit < 0.30:
            return None

        margin_pct = round(metrics.profit_margin_per_unit * 100, 1)

        return Recommendation(
            category="promotional",
            priority="LOW",
            title=f"Promote low-traffic product '{product.name}'",
            description=(
                f"'{product.name}' has only {product.total_views} views but "
                f"a healthy {margin_pct}% profit margin. Running a Shopee "
                "Ads campaign or joining a flash sale can increase visibility "
                "while still preserving profitability."
            ),
            supporting_products=[product.product_id],
            supporting_metrics={
                "total_views": product.total_views,
                "profit_margin_per_unit": metrics.profit_margin_per_unit,
            },
            estimated_impact=(
                "Increasing visibility through promotions can drive new "
                "traffic to a profitable product at low risk."
            ),
        )
