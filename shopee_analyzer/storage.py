"""Persistent data store for the Shopee Sales Analyzer.

Uses SQLite (WAL journal mode) as the default backend.  All write operations
are decorated with @retry_with_backoff(max_attempts=3, delay_seconds=2) per
Requirement 9.4.  After all retries are exhausted the method logs a fatal
ERROR and raises DataStoreWriteError so callers can decide how to proceed.

Requirements: 2.4, 7.4, 9.4
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Optional

from shopee_analyzer.exceptions import DataStoreWriteError
from shopee_analyzer.models import (
    Order,
    OrderItem,
    Product,
    RefreshHistoryEntry,
    SalesPeriod,
    TokenPair,
)
from shopee_analyzer.utils import retry_with_backoff

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tokens (
    shop_id       INTEGER PRIMARY KEY,
    access_token  TEXT    NOT NULL,
    refresh_token TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT    PRIMARY KEY,
    status         TEXT    NOT NULL,
    total_payment  TEXT    NOT NULL,
    shipping_fee   TEXT    NOT NULL,
    discount_total TEXT    NOT NULL,
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT    NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id   INTEGER NOT NULL,
    product_name TEXT    NOT NULL,
    quantity     INTEGER NOT NULL,
    unit_price   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);

CREATE TABLE IF NOT EXISTS products (
    product_id       INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    category         TEXT    NOT NULL,
    current_price    TEXT    NOT NULL,
    stock_quantity   INTEGER NOT NULL,
    review_count     INTEGER NOT NULL,
    average_rating   REAL    NOT NULL,
    total_units_sold INTEGER NOT NULL,
    total_views      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_history (
    run_id            TEXT    PRIMARY KEY,
    run_timestamp     TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    records_collected INTEGER NOT NULL,
    duration_seconds  REAL    NOT NULL,
    failure_reason    TEXT
);

CREATE INDEX IF NOT EXISTS idx_refresh_history_timestamp
    ON refresh_history(run_timestamp);
"""


# ---------------------------------------------------------------------------
# Helper: datetime <-> ISO string
# ---------------------------------------------------------------------------

def _dt_to_str(dt: datetime) -> str:
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# DataStore
# ---------------------------------------------------------------------------

class DataStore:
    """SQLite-backed persistence layer for all Shopee Sales Analyzer entities.

    Requirements: 2.4, 7.4, 9.4
    """

    def __init__(self, db_path: str) -> None:
        """Open (or create) the SQLite database at *db_path* and apply the schema.

        Args:
            db_path: File-system path to the SQLite database file.
                     Pass ``":memory:"`` for an in-process test database.
        """
        self._db_path = db_path
        # For in-memory databases, keep a single persistent connection so the
        # schema and data survive across multiple _connect() calls.  Each new
        # sqlite3.connect(":memory:") would return a fresh, empty database.
        self._memory_conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.execute("PRAGMA foreign_keys = ON")
            self._memory_conn.row_factory = sqlite3.Row
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return a SQLite connection with sensible defaults.

        For in-memory databases, returns the single persistent connection so
        that the schema and data survive across multiple calls.
        """
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they don't already exist."""
        with self._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Token operations
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3, delay_seconds=2, exceptions=(Exception,))
    def save_token(self, token: TokenPair) -> None:
        """Upsert the token pair for *token.shop_id*.

        Requirements: 2.4, 9.4
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tokens (shop_id, access_token, refresh_token, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(shop_id) DO UPDATE SET
                        access_token  = excluded.access_token,
                        refresh_token = excluded.refresh_token,
                        expires_at    = excluded.expires_at
                    """,
                    (
                        token.shop_id,
                        token.access_token,
                        token.refresh_token,
                        _dt_to_str(token.expires_at),
                    ),
                )
        except Exception as exc:
            logger.error(
                "Fatal error saving token for shop_id=%s after all retries: %s",
                token.shop_id,
                exc,
            )
            raise DataStoreWriteError(
                f"Failed to save token for shop_id={token.shop_id}"
            ) from exc

    def load_token(self, shop_id: int) -> Optional[TokenPair]:
        """Return the stored :class:`TokenPair` for *shop_id*, or ``None``.

        Requirements: 2.4
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT access_token, refresh_token, expires_at, shop_id "
                "FROM tokens WHERE shop_id = ?",
                (shop_id,),
            ).fetchone()

        if row is None:
            return None

        return TokenPair(
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=_str_to_dt(row["expires_at"]),
            shop_id=row["shop_id"],
        )

    # ------------------------------------------------------------------
    # Order operations
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3, delay_seconds=2, exceptions=(Exception,))
    def save_orders(self, orders: list[Order]) -> None:
        """Insert or replace *orders* and their line items.

        Existing orders (matched by order_id) are fully replaced.

        Requirements: 2.4, 9.4
        """
        try:
            with self._connect() as conn:
                for order in orders:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO orders
                            (order_id, status, total_payment, shipping_fee,
                             discount_total, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            order.order_id,
                            order.status,
                            str(order.total_payment),
                            str(order.shipping_fee),
                            str(order.discount_total),
                            _dt_to_str(order.created_at),
                        ),
                    )
                    # Remove old items first (cascade would handle DELETE, but
                    # INSERT OR REPLACE on the parent does not cascade here).
                    conn.execute(
                        "DELETE FROM order_items WHERE order_id = ?",
                        (order.order_id,),
                    )
                    conn.executemany(
                        """
                        INSERT INTO order_items
                            (order_id, product_id, product_name, quantity, unit_price)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                order.order_id,
                                item.product_id,
                                item.product_name,
                                item.quantity,
                                str(item.unit_price),
                            )
                            for item in order.items
                        ],
                    )
        except Exception as exc:
            logger.error(
                "Fatal error saving %d order(s) after all retries: %s",
                len(orders),
                exc,
            )
            raise DataStoreWriteError(
                f"Failed to save {len(orders)} order(s)"
            ) from exc

    def load_orders(self, period: SalesPeriod) -> list[Order]:
        """Return all :class:`Order` objects whose ``created_at`` falls within *period*.

        Requirements: 2.4
        """
        with self._connect() as conn:
            order_rows = conn.execute(
                """
                SELECT order_id, status, total_payment, shipping_fee,
                       discount_total, created_at
                FROM orders
                WHERE created_at >= ? AND created_at <= ?
                ORDER BY created_at
                """,
                (_dt_to_str(period.start), _dt_to_str(period.end)),
            ).fetchall()

            orders: list[Order] = []
            for row in order_rows:
                item_rows = conn.execute(
                    """
                    SELECT product_id, product_name, quantity, unit_price
                    FROM order_items
                    WHERE order_id = ?
                    """,
                    (row["order_id"],),
                ).fetchall()

                items = [
                    OrderItem(
                        product_id=ir["product_id"],
                        product_name=ir["product_name"],
                        quantity=ir["quantity"],
                        unit_price=Decimal(ir["unit_price"]),
                    )
                    for ir in item_rows
                ]

                orders.append(
                    Order(
                        order_id=row["order_id"],
                        status=row["status"],
                        items=items,
                        total_payment=Decimal(row["total_payment"]),
                        shipping_fee=Decimal(row["shipping_fee"]),
                        discount_total=Decimal(row["discount_total"]),
                        created_at=_str_to_dt(row["created_at"]),
                    )
                )

        return orders

    # ------------------------------------------------------------------
    # Product operations
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3, delay_seconds=2, exceptions=(Exception,))
    def save_products(self, products: list[Product]) -> None:
        """Insert or replace *products*.

        Requirements: 2.4, 9.4
        """
        try:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO products
                        (product_id, name, category, current_price,
                         stock_quantity, review_count, average_rating,
                         total_units_sold, total_views)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            p.product_id,
                            p.name,
                            p.category,
                            str(p.current_price),
                            p.stock_quantity,
                            p.review_count,
                            p.average_rating,
                            p.total_units_sold,
                            p.total_views,
                        )
                        for p in products
                    ],
                )
        except Exception as exc:
            logger.error(
                "Fatal error saving %d product(s) after all retries: %s",
                len(products),
                exc,
            )
            raise DataStoreWriteError(
                f"Failed to save {len(products)} product(s)"
            ) from exc

    def load_products(self) -> list[Product]:
        """Return all stored :class:`Product` objects.

        Requirements: 2.4
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT product_id, name, category, current_price,
                       stock_quantity, review_count, average_rating,
                       total_units_sold, total_views
                FROM products
                ORDER BY product_id
                """
            ).fetchall()

        return [
            Product(
                product_id=row["product_id"],
                name=row["name"],
                category=row["category"],
                current_price=Decimal(row["current_price"]),
                stock_quantity=row["stock_quantity"],
                review_count=row["review_count"],
                average_rating=row["average_rating"],
                total_units_sold=row["total_units_sold"],
                total_views=row["total_views"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Refresh history operations
    # ------------------------------------------------------------------

    @retry_with_backoff(max_attempts=3, delay_seconds=2, exceptions=(Exception,))
    def save_refresh_history(self, entry: RefreshHistoryEntry) -> None:
        """Append a single refresh history row.

        Requirements: 7.4, 9.4
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO refresh_history
                        (run_id, run_timestamp, status, records_collected,
                         duration_seconds, failure_reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.run_id,
                        _dt_to_str(entry.run_timestamp),
                        entry.status,
                        entry.records_collected,
                        entry.duration_seconds,
                        entry.failure_reason,
                    ),
                )
        except Exception as exc:
            logger.error(
                "Fatal error saving refresh history entry run_id=%s after all retries: %s",
                entry.run_id,
                exc,
            )
            raise DataStoreWriteError(
                f"Failed to save refresh history entry run_id={entry.run_id}"
            ) from exc

    def load_refresh_history(self) -> list[RefreshHistoryEntry]:
        """Return all refresh history entries ordered by timestamp ascending.

        Requirements: 7.4
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, run_timestamp, status, records_collected,
                       duration_seconds, failure_reason
                FROM refresh_history
                ORDER BY run_timestamp ASC
                """
            ).fetchall()

        return [
            RefreshHistoryEntry(
                run_id=row["run_id"],
                run_timestamp=_str_to_dt(row["run_timestamp"]),
                status=row["status"],
                records_collected=row["records_collected"],
                duration_seconds=row["duration_seconds"],
                failure_reason=row["failure_reason"],
            )
            for row in rows
        ]

    def get_last_successful_run(self) -> Optional[datetime]:
        """Return the timestamp of the most recent SUCCESS entry, or ``None``.

        Requirements: 7.2, 7.4
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_timestamp
                FROM refresh_history
                WHERE status = 'SUCCESS'
                ORDER BY run_timestamp DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return _str_to_dt(row["run_timestamp"])
