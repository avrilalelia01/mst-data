"""Unit tests for shopee_analyzer.storage.DataStore.

Tests cover all CRUD methods, WAL mode, retry behaviour on write failures,
and DataStoreWriteError being raised after exhausted retries.

Requirements: 2.4, 7.4, 9.4
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from shopee_analyzer.exceptions import DataStoreWriteError
from shopee_analyzer.models import (
    Order,
    OrderItem,
    Product,
    RefreshHistoryEntry,
    SalesPeriod,
    TokenPair,
)
from shopee_analyzer.storage import DataStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """Return a DataStore backed by a temporary SQLite database."""
    return DataStore(str(tmp_path / "test.db"))


@pytest.fixture
def token():
    return TokenPair(
        access_token="acc123",
        refresh_token="ref456",
        expires_at=datetime(2030, 1, 1, 12, 0, 0),
        shop_id=99,
    )


@pytest.fixture
def product():
    return Product(
        product_id=1001,
        name="Widget",
        category="Electronics",
        current_price=Decimal("29.99"),
        stock_quantity=50,
        review_count=120,
        average_rating=4.5,
        total_units_sold=300,
        total_views=5000,
    )


def _make_order(order_id: str, created_at: datetime) -> Order:
    return Order(
        order_id=order_id,
        status="COMPLETED",
        items=[
            OrderItem(
                product_id=1001,
                product_name="Widget",
                quantity=2,
                unit_price=Decimal("29.99"),
            )
        ],
        total_payment=Decimal("59.98"),
        shipping_fee=Decimal("3.00"),
        discount_total=Decimal("0.00"),
        created_at=created_at,
    )


def _make_history_entry(
    run_id: str,
    status: str = "SUCCESS",
    records: int = 10,
    duration: float = 1.5,
    failure_reason: str | None = None,
    offset_seconds: int = 0,
) -> RefreshHistoryEntry:
    return RefreshHistoryEntry(
        run_id=run_id,
        run_timestamp=datetime(2024, 6, 1, 10, 0, 0) + timedelta(seconds=offset_seconds),
        status=status,
        records_collected=records,
        duration_seconds=duration,
        failure_reason=failure_reason,
    )


# ---------------------------------------------------------------------------
# Schema / initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_db_file(self, tmp_path):
        db_file = tmp_path / "new.db"
        assert not db_file.exists()
        DataStore(str(db_file))
        assert db_file.exists()

    def test_wal_journal_mode(self, tmp_path):
        DataStore(str(tmp_path / "wal.db"))
        conn = sqlite3.connect(str(tmp_path / "wal.db"))
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert row[0] == "wal"

    def test_all_tables_exist(self, store):
        conn = sqlite3.connect(store._db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        expected = {"tokens", "orders", "order_items", "products", "refresh_history"}
        assert expected.issubset(tables)

    def test_idempotent_init(self, tmp_path):
        """Calling DataStore twice on the same file should not raise."""
        path = str(tmp_path / "idem.db")
        DataStore(path)
        DataStore(path)  # should not raise


# ---------------------------------------------------------------------------
# Token operations
# ---------------------------------------------------------------------------

class TestTokenOperations:
    def test_save_and_load_token(self, store, token):
        store.save_token(token)
        loaded = store.load_token(token.shop_id)
        assert loaded is not None
        assert loaded.access_token == token.access_token
        assert loaded.refresh_token == token.refresh_token
        assert loaded.expires_at == token.expires_at
        assert loaded.shop_id == token.shop_id

    def test_load_token_missing_returns_none(self, store):
        assert store.load_token(999) is None

    def test_save_token_upsert(self, store, token):
        store.save_token(token)
        updated = TokenPair(
            access_token="new_acc",
            refresh_token="new_ref",
            expires_at=datetime(2031, 1, 1),
            shop_id=token.shop_id,
        )
        store.save_token(updated)
        loaded = store.load_token(token.shop_id)
        assert loaded.access_token == "new_acc"
        assert loaded.refresh_token == "new_ref"

    def test_save_token_raises_write_error_after_retries(self, store, token):
        """DataStoreWriteError must bubble up when the write always fails."""
        with patch.object(store, "_connect", side_effect=sqlite3.OperationalError("disk full")):
            with pytest.raises(DataStoreWriteError):
                store.save_token(token)


# ---------------------------------------------------------------------------
# Order operations
# ---------------------------------------------------------------------------

class TestOrderOperations:
    def test_save_and_load_orders(self, store):
        now = datetime(2024, 3, 15, 8, 0, 0)
        order = _make_order("ORD001", now)
        period = SalesPeriod(
            start=datetime(2024, 3, 1),
            end=datetime(2024, 3, 31),
        )
        store.save_orders([order])
        loaded = store.load_orders(period)
        assert len(loaded) == 1
        o = loaded[0]
        assert o.order_id == "ORD001"
        assert o.status == "COMPLETED"
        assert o.total_payment == Decimal("59.98")
        assert len(o.items) == 1
        assert o.items[0].product_id == 1001
        assert o.items[0].quantity == 2

    def test_load_orders_filters_by_period(self, store):
        store.save_orders([
            _make_order("A", datetime(2024, 1, 15)),
            _make_order("B", datetime(2024, 2, 15)),
            _make_order("C", datetime(2024, 3, 15)),
        ])
        period = SalesPeriod(
            start=datetime(2024, 2, 1),
            end=datetime(2024, 2, 28),
        )
        loaded = store.load_orders(period)
        assert [o.order_id for o in loaded] == ["B"]

    def test_save_orders_replaces_existing(self, store):
        order = _make_order("ORD001", datetime(2024, 3, 15))
        store.save_orders([order])
        # Replace with new status and single item
        updated = Order(
            order_id="ORD001",
            status="CANCELLED",
            items=[
                OrderItem(
                    product_id=2002,
                    product_name="Gadget",
                    quantity=1,
                    unit_price=Decimal("10.00"),
                )
            ],
            total_payment=Decimal("10.00"),
            shipping_fee=Decimal("1.00"),
            discount_total=Decimal("0.00"),
            created_at=datetime(2024, 3, 15),
        )
        store.save_orders([updated])
        period = SalesPeriod(start=datetime(2024, 3, 1), end=datetime(2024, 3, 31))
        loaded = store.load_orders(period)
        assert len(loaded) == 1
        assert loaded[0].status == "CANCELLED"
        assert len(loaded[0].items) == 1
        assert loaded[0].items[0].product_id == 2002

    def test_save_orders_raises_write_error_after_retries(self, store):
        order = _make_order("ORD001", datetime(2024, 3, 15))
        with patch.object(store, "_connect", side_effect=sqlite3.OperationalError("locked")):
            with pytest.raises(DataStoreWriteError):
                store.save_orders([order])

    def test_load_orders_empty_period(self, store):
        period = SalesPeriod(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 31),
        )
        assert store.load_orders(period) == []


# ---------------------------------------------------------------------------
# Product operations
# ---------------------------------------------------------------------------

class TestProductOperations:
    def test_save_and_load_products(self, store, product):
        store.save_products([product])
        loaded = store.load_products()
        assert len(loaded) == 1
        p = loaded[0]
        assert p.product_id == product.product_id
        assert p.name == product.name
        assert p.current_price == product.current_price
        assert p.average_rating == product.average_rating

    def test_load_products_empty(self, store):
        assert store.load_products() == []

    def test_save_products_replaces_existing(self, store, product):
        store.save_products([product])
        updated = Product(
            product_id=product.product_id,
            name="Widget v2",
            category="Electronics",
            current_price=Decimal("39.99"),
            stock_quantity=0,
            review_count=200,
            average_rating=4.8,
            total_units_sold=600,
            total_views=10000,
        )
        store.save_products([updated])
        loaded = store.load_products()
        assert len(loaded) == 1
        assert loaded[0].name == "Widget v2"
        assert loaded[0].stock_quantity == 0

    def test_save_multiple_products(self, store):
        products = [
            Product(
                product_id=i,
                name=f"Product {i}",
                category="Cat",
                current_price=Decimal("1.00"),
                stock_quantity=10,
                review_count=5,
                average_rating=4.0,
                total_units_sold=20,
                total_views=100,
            )
            for i in range(1, 6)
        ]
        store.save_products(products)
        loaded = store.load_products()
        assert len(loaded) == 5

    def test_save_products_raises_write_error_after_retries(self, store, product):
        with patch.object(store, "_connect", side_effect=sqlite3.OperationalError("locked")):
            with pytest.raises(DataStoreWriteError):
                store.save_products([product])


# ---------------------------------------------------------------------------
# Refresh history operations
# ---------------------------------------------------------------------------

class TestRefreshHistoryOperations:
    def test_save_and_load_refresh_history(self, store):
        entry = _make_history_entry("run-1")
        store.save_refresh_history(entry)
        history = store.load_refresh_history()
        assert len(history) == 1
        h = history[0]
        assert h.run_id == "run-1"
        assert h.status == "SUCCESS"
        assert h.records_collected == 10
        assert h.failure_reason is None

    def test_load_refresh_history_ordered_by_timestamp(self, store):
        store.save_refresh_history(_make_history_entry("run-3", offset_seconds=20))
        store.save_refresh_history(_make_history_entry("run-1", offset_seconds=0))
        store.save_refresh_history(_make_history_entry("run-2", offset_seconds=10))
        history = store.load_refresh_history()
        assert [h.run_id for h in history] == ["run-1", "run-2", "run-3"]

    def test_load_refresh_history_empty(self, store):
        assert store.load_refresh_history() == []

    def test_save_failure_entry_with_reason(self, store):
        entry = _make_history_entry(
            "run-fail",
            status="FAILURE",
            failure_reason="ConnectionError: timeout",
        )
        store.save_refresh_history(entry)
        loaded = store.load_refresh_history()[0]
        assert loaded.status == "FAILURE"
        assert loaded.failure_reason == "ConnectionError: timeout"

    def test_save_refresh_history_raises_write_error_after_retries(self, store):
        entry = _make_history_entry("run-err")
        with patch.object(store, "_connect", side_effect=sqlite3.OperationalError("locked")):
            with pytest.raises(DataStoreWriteError):
                store.save_refresh_history(entry)


# ---------------------------------------------------------------------------
# get_last_successful_run
# ---------------------------------------------------------------------------

class TestGetLastSuccessfulRun:
    def test_returns_none_when_no_history(self, store):
        assert store.get_last_successful_run() is None

    def test_returns_none_when_only_failures(self, store):
        store.save_refresh_history(_make_history_entry("r1", status="FAILURE"))
        assert store.get_last_successful_run() is None

    def test_returns_most_recent_success(self, store):
        store.save_refresh_history(_make_history_entry("r1", status="SUCCESS", offset_seconds=0))
        store.save_refresh_history(_make_history_entry("r2", status="SUCCESS", offset_seconds=60))
        store.save_refresh_history(_make_history_entry("r3", status="FAILURE", offset_seconds=120))
        result = store.get_last_successful_run()
        expected = datetime(2024, 6, 1, 10, 0, 0) + timedelta(seconds=60)
        assert result == expected

    def test_ignores_failures_when_finding_last_success(self, store):
        store.save_refresh_history(_make_history_entry("r1", status="FAILURE", offset_seconds=200))
        store.save_refresh_history(_make_history_entry("r2", status="SUCCESS", offset_seconds=100))
        result = store.get_last_successful_run()
        expected = datetime(2024, 6, 1, 10, 0, 0) + timedelta(seconds=100)
        assert result == expected
