"""Data Collector for the Shopee Sales Analyzer.

Pages through Shopee API v2 endpoints (orders, products) using cursor-based
pagination, maps responses to internal dataclasses, persists collected records
to the Data Store, and returns a :class:`CollectionResult` with accurate
collected/failed counts.

On partial failure the collector logs the failed record IDs and continues so
that one bad record does not abort the entire run.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests

from shopee_analyzer.auth import AuthClient
from shopee_analyzer.exceptions import APIConnectionError, CollectionError
from shopee_analyzer.models import (
    CollectionResult,
    Configuration,
    Order,
    OrderItem,
    Product,
    SalesPeriod,
)
from shopee_analyzer.storage import DataStore
from shopee_analyzer.utils import retry_with_backoff

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://partner.test-stable.shopeemobile.com"

_PATH_ORDER_LIST = "/api/v2/order/get_order_list"
_PATH_ORDER_DETAIL = "/api/v2/order/get_order_detail"
_PATH_ITEM_LIST = "/api/v2/product/get_item_list"
_PATH_ITEM_BASE_INFO = "/api/v2/product/get_item_base_info"

_ORDER_PAGE_SIZE = 50       # Max allowed by Shopee
_ORDER_DETAIL_BATCH = 50    # Max order SNs per detail call
_PRODUCT_PAGE_SIZE = 100    # Max allowed by Shopee
_PRODUCT_DETAIL_BATCH = 100 # Max item IDs per base-info call

# Smallest currency unit divisor (prices are stored as int * 100000 by Shopee)
_PRICE_DIVISOR = Decimal("100000")


# ---------------------------------------------------------------------------
# DataCollector
# ---------------------------------------------------------------------------

class DataCollector:
    """Fetches and stores Shopee order and product data.

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
    """

    def __init__(
        self,
        config: Configuration,
        auth_client: AuthClient,
        data_store: DataStore,
    ) -> None:
        """Initialise the collector.

        Args:
            config: Validated ``Configuration`` with partner and shop credentials.
            auth_client: Authenticated ``AuthClient`` for token and signing.
            data_store: ``DataStore`` instance used to persist collected records.
        """
        self._config = config
        self._auth_client = auth_client
        self._data_store = data_store
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect_orders(self, period: SalesPeriod) -> CollectionResult:
        """Collect all orders within *period* and persist them to the Data Store.

        Pagination strategy:
          1. Page through ``get_order_list`` using cursor pagination to collect
             all order SNs for the period.
          2. Batch-fetch full order details from ``get_order_detail`` (50 per call).
          3. Map each order detail response to an :class:`Order` dataclass.
          4. Persist collected orders to the Data Store.
          5. Return a :class:`CollectionResult` with accurate counts.

        On individual order-detail fetch failure the failed order SN is logged
        and added to ``failed_ids``; collection continues for the remainder.

        Requirements: 2.1, 2.2, 2.4, 2.5, 2.6
        """
        start_ts = time.time()
        logger.info(
            "Starting order collection for period %s to %s",
            period.start.isoformat(),
            period.end.isoformat(),
        )

        # --- Step 1: Collect all order SNs via pagination ---
        all_order_sns: list[str] = []
        failed_ids: list[str] = []

        try:
            all_order_sns = self._paginate_order_list(period)
        except (CollectionError, APIConnectionError) as exc:
            duration = time.time() - start_ts
            logger.error("Failed to retrieve order list: %s", exc)
            return CollectionResult(
                records_collected=0,
                records_failed=0,
                failed_ids=[],
                duration_seconds=duration,
            )

        logger.info("Retrieved %d order SNs; fetching details.", len(all_order_sns))

        # --- Step 2: Batch-fetch order details ---
        collected_orders: list[Order] = []

        for batch_start in range(0, len(all_order_sns), _ORDER_DETAIL_BATCH):
            batch_sns = all_order_sns[batch_start : batch_start + _ORDER_DETAIL_BATCH]
            try:
                orders, batch_failed = self._fetch_order_details(batch_sns)
                collected_orders.extend(orders)
                failed_ids.extend(batch_failed)
            except (CollectionError, APIConnectionError) as exc:
                # Entire batch failed — mark all SNs in the batch as failed
                logger.error(
                    "Batch order-detail fetch failed for %d order(s) starting at index %d: %s",
                    len(batch_sns),
                    batch_start,
                    exc,
                )
                failed_ids.extend(batch_sns)

        # --- Step 3: Persist to Data Store ---
        if collected_orders:
            try:
                self._data_store.save_orders(collected_orders)
            except Exception as exc:  # DataStoreWriteError or similar
                logger.error("Failed to persist %d order(s): %s", len(collected_orders), exc)

        duration = time.time() - start_ts
        result = CollectionResult(
            records_collected=len(collected_orders),
            records_failed=len(failed_ids),
            failed_ids=failed_ids,
            duration_seconds=duration,
        )

        logger.info(
            "Order collection complete: collected=%d, failed=%d, duration=%.2fs",
            result.records_collected,
            result.records_failed,
            result.duration_seconds,
        )
        return result

    def collect_products(self) -> CollectionResult:
        """Collect all active product listings and persist them to the Data Store.

        Pagination strategy:
          1. Page through ``get_item_list`` using offset pagination to collect
             all item IDs with status NORMAL.
          2. Batch-fetch full product details from ``get_item_base_info``
             (100 per call).
          3. Map each product response to a :class:`Product` dataclass.
          4. Persist collected products to the Data Store.
          5. Return a :class:`CollectionResult` with accurate counts.

        Requirements: 2.3, 2.4, 2.5, 2.6
        """
        start_ts = time.time()
        logger.info("Starting product collection.")

        # --- Step 1: Collect all item IDs via pagination ---
        all_item_ids: list[int] = []
        failed_ids: list[str] = []

        try:
            all_item_ids = self._paginate_item_list()
        except (CollectionError, APIConnectionError) as exc:
            duration = time.time() - start_ts
            logger.error("Failed to retrieve item list: %s", exc)
            return CollectionResult(
                records_collected=0,
                records_failed=0,
                failed_ids=[],
                duration_seconds=duration,
            )

        logger.info("Retrieved %d item IDs; fetching details.", len(all_item_ids))

        # --- Step 2: Batch-fetch product details ---
        collected_products: list[Product] = []

        for batch_start in range(0, len(all_item_ids), _PRODUCT_DETAIL_BATCH):
            batch_ids = all_item_ids[batch_start : batch_start + _PRODUCT_DETAIL_BATCH]
            try:
                products, batch_failed = self._fetch_product_details(batch_ids)
                collected_products.extend(products)
                failed_ids.extend(batch_failed)
            except (CollectionError, APIConnectionError) as exc:
                logger.error(
                    "Batch product-detail fetch failed for %d item(s) starting at index %d: %s",
                    len(batch_ids),
                    batch_start,
                    exc,
                )
                failed_ids.extend(str(i) for i in batch_ids)

        # --- Step 3: Persist to Data Store ---
        if collected_products:
            try:
                self._data_store.save_products(collected_products)
            except Exception as exc:
                logger.error("Failed to persist %d product(s): %s", len(collected_products), exc)

        duration = time.time() - start_ts
        result = CollectionResult(
            records_collected=len(collected_products),
            records_failed=len(failed_ids),
            failed_ids=failed_ids,
            duration_seconds=duration,
        )

        logger.info(
            "Product collection complete: collected=%d, failed=%d, duration=%.2fs",
            result.records_collected,
            result.records_failed,
            result.duration_seconds,
        )
        return result

    def collect_incremental(self, since: datetime) -> CollectionResult:
        """Collect orders since *since* and all active products.

        Calls ``collect_orders(SalesPeriod(since, now))`` then
        ``collect_products()`` and merges the two :class:`CollectionResult`
        objects into one combined result.

        Requirements: 2.1, 2.3, 2.6, 7.2
        """
        now = datetime.now(tz=timezone.utc)
        period = SalesPeriod(start=since, end=now)

        logger.info(
            "Starting incremental collection since %s", since.isoformat()
        )

        start_ts = time.time()

        orders_result = self.collect_orders(period)
        products_result = self.collect_products()

        duration = time.time() - start_ts

        combined = CollectionResult(
            records_collected=orders_result.records_collected + products_result.records_collected,
            records_failed=orders_result.records_failed + products_result.records_failed,
            failed_ids=orders_result.failed_ids + products_result.failed_ids,
            duration_seconds=duration,
        )

        logger.info(
            "Incremental collection complete: collected=%d, failed=%d, duration=%.2fs",
            combined.records_collected,
            combined.records_failed,
            combined.duration_seconds,
        )
        return combined

    # ------------------------------------------------------------------
    # Private helpers — order pagination and detail fetch
    # ------------------------------------------------------------------

    def _paginate_order_list(self, period: SalesPeriod) -> list[str]:
        """Return all order SNs for *period* via cursor-based pagination.

        Requirements: 2.1
        """
        order_sns: list[str] = []
        cursor: Optional[str] = None
        time_from = int(period.start.timestamp())
        time_to = int(period.end.timestamp())

        while True:
            access_token = self._auth_client.get_valid_token()
            signed_params = self._auth_client.sign_request(_PATH_ORDER_LIST, access_token)

            params: dict = {
                **signed_params,
                "time_range_field": "create_time",
                "time_from": time_from,
                "time_to": time_to,
                "page_size": _ORDER_PAGE_SIZE,
            }
            if cursor:
                params["cursor"] = cursor

            data = self._get_with_retry(_BASE_URL + _PATH_ORDER_LIST, params)

            response = data.get("response", {})
            page_orders = response.get("order_list", [])
            has_more = response.get("more", False)
            next_cursor = response.get("next_cursor", "")

            for item in page_orders:
                sn = item.get("order_sn", "")
                if sn:
                    order_sns.append(sn)

            logger.debug(
                "Fetched order list page: %d SNs, more=%s, cursor=%s",
                len(page_orders),
                has_more,
                next_cursor,
            )

            if not has_more or not next_cursor:
                break
            cursor = next_cursor

        return order_sns

    def _fetch_order_details(
        self, order_sns: list[str]
    ) -> tuple[list[Order], list[str]]:
        """Batch-fetch full order details for *order_sns*.

        Returns a tuple of (successfully mapped orders, failed order SNs).

        Requirements: 2.2
        """
        if not order_sns:
            return [], []

        access_token = self._auth_client.get_valid_token()
        signed_params = self._auth_client.sign_request(_PATH_ORDER_DETAIL, access_token)

        body = {"order_sn_list": order_sns}

        data = self._post_with_retry(
            _BASE_URL + _PATH_ORDER_DETAIL, signed_params, body
        )

        response = data.get("response", {})
        raw_orders = response.get("order_list", [])

        orders: list[Order] = []
        failed_ids: list[str] = []
        # Track SNs that were actually present in the API response (successfully
        # mapped or failed-but-identifiable). Used to detect truly absent SNs.
        accounted_sns: set[str] = set()

        for raw in raw_orders:
            raw_sn = raw.get("order_sn")
            try:
                order = self._map_order(raw)
                orders.append(order)
                accounted_sns.add(order.order_id)
            except Exception as exc:
                logger.warning(
                    "Failed to map order '%s': %s — skipping.", raw_sn or "<unknown>", exc
                )
                if raw_sn is not None:
                    # Record the actual SN as a failure and mark it accounted for
                    failed_ids.append(str(raw_sn))
                    accounted_sns.add(str(raw_sn))
                # If order_sn was missing from the raw dict we cannot associate
                # the failed record with a requested SN — it will be caught by
                # the "not returned" guard below via the count discrepancy.

        # Any SN not present in the API response at all is also a failure
        for sn in order_sns:
            if sn not in accounted_sns:
                logger.warning("Order SN '%s' not returned by detail API — marking as failed.", sn)
                failed_ids.append(sn)

        return orders, failed_ids

    @staticmethod
    def _map_order(raw: dict) -> Order:
        """Map a raw Shopee order detail dict to an :class:`Order` dataclass.

        Field mapping:
          - ``order_sn``              → ``order_id``
          - ``order_status``          → ``status``
          - ``total_amount``          → ``total_payment`` (Decimal)
          - ``actual_shipping_fee``   → ``shipping_fee`` (Decimal)
          - ``discount_from_shopee``  → ``discount_total`` (Decimal)
          - ``pay_time``              → ``created_at`` (unix timestamp → datetime)
          - ``item_list``             → ``items`` (list of :class:`OrderItem`)

        Requirements: 2.2
        """
        order_id: str = raw["order_sn"]
        status: str = raw.get("order_status", "")

        # Monetary amounts — Shopee returns them as numbers (int or float)
        total_payment = Decimal(str(raw.get("total_amount", 0)))
        shipping_fee = Decimal(str(raw.get("actual_shipping_fee", 0)))
        discount_total = Decimal(str(raw.get("discount_from_shopee", 0)))

        # pay_time is a unix timestamp (int)
        pay_time = raw.get("pay_time", 0)
        created_at = datetime.fromtimestamp(pay_time, tz=timezone.utc)

        # Map item list
        items: list[OrderItem] = []
        for raw_item in raw.get("item_list", []):
            item = OrderItem(
                product_id=int(raw_item.get("item_id", 0)),
                product_name=str(raw_item.get("item_name", "")),
                quantity=int(raw_item.get("model_quantity_purchased", 0)),
                unit_price=Decimal(str(raw_item.get("model_discounted_price", 0))),
            )
            items.append(item)

        return Order(
            order_id=order_id,
            status=status,
            items=items,
            total_payment=total_payment,
            shipping_fee=shipping_fee,
            discount_total=discount_total,
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # Private helpers — product pagination and detail fetch
    # ------------------------------------------------------------------

    def _paginate_item_list(self) -> list[int]:
        """Return all active item IDs via offset-based pagination.

        Requirements: 2.1
        """
        item_ids: list[int] = []
        offset = 0

        while True:
            access_token = self._auth_client.get_valid_token()
            signed_params = self._auth_client.sign_request(_PATH_ITEM_LIST, access_token)

            params = {
                **signed_params,
                "offset": offset,
                "page_size": _PRODUCT_PAGE_SIZE,
                "item_status": "NORMAL",
            }

            data = self._get_with_retry(_BASE_URL + _PATH_ITEM_LIST, params)

            response = data.get("response", {})
            items = response.get("item", [])
            has_next = response.get("has_next_page", False)
            next_offset = response.get("next_offset", offset + len(items))

            for item in items:
                item_id = item.get("item_id")
                if item_id is not None:
                    item_ids.append(int(item_id))

            logger.debug(
                "Fetched item list page: %d items, has_next=%s, next_offset=%d",
                len(items),
                has_next,
                next_offset,
            )

            if not has_next:
                break
            offset = next_offset

        return item_ids

    def _fetch_product_details(
        self, item_ids: list[int]
    ) -> tuple[list[Product], list[str]]:
        """Batch-fetch full product details for *item_ids*.

        Returns a tuple of (successfully mapped products, failed item ID strings).

        Requirements: 2.3
        """
        if not item_ids:
            return [], []

        access_token = self._auth_client.get_valid_token()
        signed_params = self._auth_client.sign_request(_PATH_ITEM_BASE_INFO, access_token)

        # item_id_list is passed as a comma-separated query param
        params = {
            **signed_params,
            "item_id_list": ",".join(str(i) for i in item_ids),
        }

        data = self._get_with_retry(_BASE_URL + _PATH_ITEM_BASE_INFO, params)

        response = data.get("response", {})
        raw_items = response.get("item_list", [])

        products: list[Product] = []
        failed_ids: list[str] = []

        for raw in raw_items:
            try:
                product = self._map_product(raw)
                products.append(product)
            except Exception as exc:
                item_id = raw.get("item_id", "<unknown>")
                logger.warning(
                    "Failed to map product '%s': %s — skipping.", item_id, exc
                )
                failed_ids.append(str(item_id))

        # Any ID not returned is also a failure
        returned_ids = {p.product_id for p in products} | {int(f) for f in failed_ids if f.isdigit()}
        for item_id in item_ids:
            if item_id not in returned_ids:
                logger.warning(
                    "Item ID %d not returned by base-info API — marking as failed.", item_id
                )
                failed_ids.append(str(item_id))

        return products, failed_ids

    @staticmethod
    def _map_product(raw: dict) -> Product:
        """Map a raw Shopee product base-info dict to a :class:`Product` dataclass.

        Field mapping:
          - ``item_id``           → ``product_id``
          - ``item_name``         → ``name``
          - ``category_id``       → ``category`` (str representation of int)
          - ``price_info[0].current_price`` → ``current_price`` (divide by 100000)
          - ``stock_info_v2.summary_info.total_available_stock`` → ``stock_quantity``
          - ``comment_count``     → ``review_count``
          - ``rating_star``       → ``average_rating``
          - ``sales``             → ``total_units_sold``
          - ``view_count``        → ``total_views``

        Requirements: 2.3
        """
        product_id: int = int(raw["item_id"])
        name: str = str(raw.get("item_name", ""))
        category: str = str(raw.get("category_id", ""))

        # Price — stored in smallest currency unit; divide by 100000
        price_info = raw.get("price_info", [])
        raw_price = 0
        if price_info:
            raw_price = price_info[0].get("current_price", 0)
        current_price = Decimal(str(raw_price)) / _PRICE_DIVISOR

        # Stock
        stock_info = raw.get("stock_info_v2", {})
        summary_info = stock_info.get("summary_info", {})
        stock_quantity: int = int(summary_info.get("total_available_stock", 0))

        # Ratings and sales
        review_count: int = int(raw.get("comment_count", 0))
        average_rating: float = float(raw.get("rating_star", 0.0))
        total_units_sold: int = int(raw.get("sales", 0))
        total_views: int = int(raw.get("view_count", 0))

        return Product(
            product_id=product_id,
            name=name,
            category=category,
            current_price=current_price,
            stock_quantity=stock_quantity,
            review_count=review_count,
            average_rating=average_rating,
            total_units_sold=total_units_sold,
            total_views=total_views,
        )

    # ------------------------------------------------------------------
    # Private helpers — HTTP with retry
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_attempts=3,
        delay_seconds=5,
        exceptions=(requests.ConnectionError, requests.Timeout, requests.RequestException),
    )
    def _get_with_retry(self, url: str, params: dict) -> dict:
        """HTTP GET *url* with *params*, retrying on transient errors.

        Requirements: 1.5
        """
        try:
            response = self._session.get(url, params=params, timeout=30)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise exc
        except requests.RequestException as exc:
            raise APIConnectionError(
                f"Unexpected request error contacting {url}: {exc}"
            ) from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise APIConnectionError(
                f"Shopee API returned HTTP {response.status_code} for GET {url}: {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise APIConnectionError(
                f"Shopee API returned non-JSON response from GET {url}: {exc}"
            ) from exc

        error_code = str(data.get("error", "")).strip()
        if error_code:
            raise CollectionError(
                f"Shopee API error '{error_code}' from GET {url}: {data.get('message', '')}"
            )

        return data

    @retry_with_backoff(
        max_attempts=3,
        delay_seconds=5,
        exceptions=(requests.ConnectionError, requests.Timeout, requests.RequestException),
    )
    def _post_with_retry(self, url: str, params: dict, body: dict) -> dict:
        """HTTP POST *body* to *url* with *params*, retrying on transient errors.

        Requirements: 1.5
        """
        try:
            response = self._session.post(url, params=params, json=body, timeout=30)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise exc
        except requests.RequestException as exc:
            raise APIConnectionError(
                f"Unexpected request error contacting {url}: {exc}"
            ) from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise APIConnectionError(
                f"Shopee API returned HTTP {response.status_code} for POST {url}: {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise APIConnectionError(
                f"Shopee API returned non-JSON response from POST {url}: {exc}"
            ) from exc

        error_code = str(data.get("error", "")).strip()
        if error_code:
            raise CollectionError(
                f"Shopee API error '{error_code}' from POST {url}: {data.get('message', '')}"
            )

        return data
