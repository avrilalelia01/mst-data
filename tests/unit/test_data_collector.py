"""Property-based tests for DataCollector data mapping.

Tests in this file use Hypothesis to verify universal properties of the
mapping functions that transform raw Shopee API responses into internal
dataclasses.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def api_product_response_strategy(draw):
    """Generate valid raw Shopee product response dicts (get_item_base_info format)."""
    item_id = draw(st.integers(min_value=1))
    item_name = draw(st.text(min_size=1))
    category_id = draw(st.integers(min_value=1))
    current_price = draw(st.integers(min_value=0))
    total_available_stock = draw(st.integers(min_value=0))
    comment_count = draw(st.integers(min_value=0))
    rating_star = draw(st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False))
    sales = draw(st.integers(min_value=0))
    view_count = draw(st.integers(min_value=0))

    return {
        "item_id": item_id,
        "item_name": item_name,
        "category_id": category_id,
        "price_info": [{"current_price": current_price}],
        "stock_info_v2": {"summary_info": {"total_available_stock": total_available_stock}},
        "comment_count": comment_count,
        "rating_star": rating_star,
        "sales": sales,
        "view_count": view_count,
    }


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 4: Product data mapping completeness
# Validates: Requirements 2.3
@given(api_product_response_strategy())
@settings(max_examples=100)
def test_product_data_mapping_completeness(raw_product):
    from shopee_analyzer.collector import DataCollector
    product = DataCollector._map_product(raw_product)
    assert product.product_id is not None
    assert product.name is not None
    assert product.category is not None
    assert product.current_price is not None
    assert product.stock_quantity is not None
    assert product.review_count is not None
    assert product.average_rating is not None
    assert product.total_units_sold is not None
    assert product.total_views is not None
    # Type checks
    assert isinstance(product.product_id, int)
    assert isinstance(product.name, str)
    assert isinstance(product.category, str)
    assert isinstance(product.average_rating, float)


# ---------------------------------------------------------------------------
# Strategy for Property 6
# ---------------------------------------------------------------------------

@st.composite
def mixed_fetch_sequence_strategy(draw):
    """Generate a list of order SNs with per-SN success/failure flags.

    Returns a list of (order_sn: str, will_succeed: bool) tuples of length
    1-20, simulating a batch where some orders map successfully and some fail.
    """
    n = draw(st.integers(min_value=1, max_value=20))
    # Produce n unique order SNs
    order_sns = draw(
        st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    # For each SN, decide whether it will succeed
    success_flags = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    return list(zip(order_sns, success_flags))


# ---------------------------------------------------------------------------
# Property 6: Collection count accuracy
# Validates: Requirements 2.6
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 6: Collection count accuracy
@given(mixed_fetch_sequence_strategy())
@settings(max_examples=100)
def test_collection_count_accuracy(fetch_sequence):
    """**Validates: Requirements 2.6**

    For any batch of N order SNs where some succeed and some fail, the number
    of successfully mapped orders plus the number of failed order SNs MUST
    always equal N.

    This is the core invariant:  records_collected + records_failed == total_attempted
    """
    from unittest.mock import patch
    from shopee_analyzer.collector import DataCollector
    from shopee_analyzer.auth import AuthClient
    from shopee_analyzer.storage import DataStore
    from shopee_analyzer.models import Configuration

    order_sns = [sn for sn, _ in fetch_sequence]
    success_map = {sn: will_succeed for sn, will_succeed in fetch_sequence}
    n = len(order_sns)

    # Build a mock API response:
    # - "will_succeed" SNs get a valid raw order dict (has order_sn key)
    # - "will_fail" SNs get a dict missing the required "order_sn" key,
    #   which causes a KeyError inside _map_order
    raw_order_list = []
    for sn in order_sns:
        if success_map[sn]:
            raw_order_list.append({
                "order_sn": sn,
                "order_status": "COMPLETED",
                "total_amount": 100,
                "actual_shipping_fee": 5,
                "discount_from_shopee": 0,
                "pay_time": 1_700_000_000,
                "item_list": [
                    {
                        "item_id": 1,
                        "item_name": "Test Item",
                        "model_quantity_purchased": 1,
                        "model_discounted_price": 100,
                    }
                ],
            })
        else:
            # Missing "order_sn" causes KeyError in _map_order → failure path
            raw_order_list.append({
                "order_status": "COMPLETED",
                "total_amount": 0,
                "actual_shipping_fee": 0,
                "discount_from_shopee": 0,
                "pay_time": 0,
                "item_list": [],
            })

    mock_response = {"response": {"order_list": raw_order_list}}

    # Create a minimal DataCollector with mock dependencies
    config = Configuration(
        partner_id=12345,
        partner_key="secret",
        shop_id=67890,
        data_store_path=":memory:",
        output_dir="./output",
        refresh_schedule="daily",
        default_period_days=30,
    )

    # Use unittest.mock to avoid needing real AuthClient / DataStore
    auth_client = AuthClient.__new__(AuthClient)
    data_store = DataStore.__new__(DataStore)

    collector = DataCollector.__new__(DataCollector)
    collector._config = config
    collector._auth_client = auth_client
    collector._data_store = data_store

    with patch.object(collector, "_post_with_retry", return_value=mock_response):
        with patch.object(auth_client, "get_valid_token", return_value="token"):
            with patch.object(auth_client, "sign_request", return_value={}):
                collected_orders, failed_ids = collector._fetch_order_details(order_sns)

    # Core invariant: collected + failed == total attempted
    assert len(collected_orders) + len(failed_ids) == n, (
        f"Count mismatch: collected={len(collected_orders)}, "
        f"failed={len(failed_ids)}, total={n}"
    )


# ---------------------------------------------------------------------------
# Strategies for paginated collection completeness (Property 5)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


@st.composite
def paginated_order_responses_strategy(draw):
    """Generate a list of 1-10 simulated cursor-based order-list API pages.

    Each page is a dict matching the structure that _paginate_order_list reads:
      - response.order_list: list of {order_sn: str}
      - response.more: True for all pages except the last
      - response.next_cursor: non-empty str for non-final pages

    Returns a list of page dicts.
    """
    num_pages = draw(st.integers(min_value=1, max_value=10))
    pages = []
    for i in range(num_pages):
        is_last = i == num_pages - 1
        num_orders = draw(st.integers(min_value=0, max_value=50))
        # Generate unique order SNs to avoid collision issues
        order_list = [{"order_sn": f"SN_{i}_{j}"} for j in range(num_orders)]
        page = {
            "response": {
                "order_list": order_list,
                "more": not is_last,
                "next_cursor": "" if is_last else f"cursor_{i}",
            }
        }
        pages.append(page)
    return pages


@st.composite
def paginated_product_responses_strategy(draw):
    """Generate a list of 1-10 simulated offset-based item-list API pages.

    Each page is a dict matching the structure that _paginate_item_list reads:
      - response.item: list of {item_id: int}
      - response.has_next_page: True for all pages except the last
      - response.next_offset: cumulative offset for non-final pages

    Returns a list of page dicts.
    """
    num_pages = draw(st.integers(min_value=1, max_value=10))
    pages = []
    offset = 0
    for i in range(num_pages):
        is_last = i == num_pages - 1
        num_items = draw(st.integers(min_value=0, max_value=100))
        item_list = [{"item_id": offset + j + 1} for j in range(num_items)]
        next_offset = offset + num_items
        page = {
            "response": {
                "item": item_list,
                "has_next_page": not is_last,
                "next_offset": next_offset,
            }
        }
        pages.append(page)
        offset = next_offset
    return pages


def _make_data_collector():
    """Build a DataCollector with a mocked AuthClient and an in-memory DataStore."""
    from shopee_analyzer.collector import DataCollector
    from shopee_analyzer.models import Configuration
    from shopee_analyzer.storage import DataStore

    config = Configuration(
        partner_id=12345,
        partner_key="test-partner-key",
        shop_id=67890,
        data_store_path=":memory:",
        output_dir="./output",
        refresh_schedule="daily",
        default_period_days=30,
    )
    data_store = DataStore(":memory:")

    auth_client = MagicMock()
    auth_client.get_valid_token.return_value = "test-access-token"
    auth_client.sign_request.return_value = {
        "partner_id": "12345",
        "timestamp": "1700000000",
        "sign": "test-sign",
        "shop_id": "67890",
        "access_token": "test-access-token",
    }

    return DataCollector(config=config, auth_client=auth_client, data_store=data_store)


# ---------------------------------------------------------------------------
# Property 5: Paginated collection completeness
# ---------------------------------------------------------------------------

# Feature: shopee-sales-analyzer, Property 5: Paginated collection completeness
# Validates: Requirements 2.1
@given(paginated_order_responses_strategy())
@settings(max_examples=100)
def test_paginated_order_collection_completeness(pages):
    """Assert that total collected order SNs equals sum of all per-page order counts."""
    collector = _make_data_collector()

    call_index = [0]

    def fake_get_with_retry(url, params):
        idx = call_index[0]
        call_index[0] += 1
        return pages[idx]

    # Patch on the instance directly so no `self` arg is needed in the fake
    with patch.object(collector, "_get_with_retry", fake_get_with_retry):
        period_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        from shopee_analyzer.models import SalesPeriod
        period = SalesPeriod(start=period_start, end=period_end)

        order_sns = collector._paginate_order_list(period)

    expected_count = sum(
        len(page["response"]["order_list"]) for page in pages
    )
    assert len(order_sns) == expected_count, (
        f"Expected {expected_count} order SNs, got {len(order_sns)}. "
        f"Pages had counts: {[len(p['response']['order_list']) for p in pages]}"
    )


# Feature: shopee-sales-analyzer, Property 5: Paginated collection completeness (products)
# Validates: Requirements 2.1
@given(paginated_product_responses_strategy())
@settings(max_examples=100)
def test_paginated_product_collection_completeness(pages):
    """Assert that total collected item IDs equals sum of all per-page item counts."""
    collector = _make_data_collector()

    call_index = [0]

    def fake_get_with_retry(url, params):
        idx = call_index[0]
        call_index[0] += 1
        return pages[idx]

    # Patch on the instance directly so no `self` arg is needed in the fake
    with patch.object(collector, "_get_with_retry", fake_get_with_retry):
        item_ids = collector._paginate_item_list()

    expected_count = sum(
        len(page["response"]["item"]) for page in pages
    )
    assert len(item_ids) == expected_count, (
        f"Expected {expected_count} item IDs, got {len(item_ids)}. "
        f"Pages had counts: {[len(p['response']['item']) for p in pages]}"
    )
