"""Database initialisation, connection helpers, and schema definitions.

Seed data is loaded from ``data/seed.json`` and inserted on first run
when tables are empty.  The schema is designed around an Indian e-commerce
context (INR currency, Indian carriers, UPI/net-banking payments, etc.).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _ensure_parent_dir(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    settings = get_settings()
    _ensure_parent_dir(settings.db_path)
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(conn: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Execute *query* and return every row as a dict."""
    rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def fetch_one(conn: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    """Execute *query* and return the first row as a dict, or ``None``."""
    row = conn.execute(query, tuple(params)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    email           TEXT    NOT NULL UNIQUE,
    phone           TEXT,
    address_line    TEXT,
    city            TEXT,
    state           TEXT,
    zip_code        TEXT,
    country         TEXT    NOT NULL DEFAULT 'India',
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    item                TEXT    NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    unit_price          REAL    NOT NULL,
    total_amount        REAL    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','processing','shipped','delivered','cancelled','returned')),
    payment_method      TEXT
                            CHECK(payment_method IN ('credit_card','debit_card','upi','net_banking','wallet','cod','emi')),
    shipping_address    TEXT,
    tracking_number     TEXT,
    ordered_at          TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS complaints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    order_id        INTEGER,
    category        TEXT    NOT NULL
                        CHECK(category IN ('delivery','billing','product','service','account','other')),
    priority        TEXT    NOT NULL DEFAULT 'medium'
                        CHECK(priority IN ('low','medium','high','critical')),
    status          TEXT    NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open','investigating','waiting_customer','resolved','closed')),
    subject         TEXT    NOT NULL,
    details         TEXT    NOT NULL,
    resolution      TEXT,
    assigned_to     TEXT,
    created_at      TEXT    NOT NULL,
    resolved_at     TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_orders_user      ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(status);
CREATE INDEX IF NOT EXISTS idx_complaints_user   ON complaints(user_id);
CREATE INDEX IF NOT EXISTS idx_complaints_order  ON complaints(order_id);
CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);
CREATE INDEX IF NOT EXISTS idx_complaints_prio   ON complaints(priority);

-- Event log tables for enriched root-cause analysis

CREATE TABLE IF NOT EXISTS payment_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    transaction_id  TEXT    NOT NULL,
    event_type      TEXT    NOT NULL
                        CHECK(event_type IN ('authorized','captured','failed','refunded','voided','chargeback','dispute_opened','dispute_resolved')),
    amount          REAL    NOT NULL,
    currency        TEXT    NOT NULL DEFAULT 'INR',
    gateway         TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'success'
                        CHECK(status IN ('success','failed','pending')),
    error_message   TEXT,
    logged_at       TEXT    NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS logistics_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    tracking_number TEXT,
    carrier         TEXT    NOT NULL,
    event_type      TEXT    NOT NULL
                        CHECK(event_type IN ('label_created','picked','packed','dispatched','in_transit','out_for_delivery','delivered','delivery_failed','returned_to_sender','held_at_facility')),
    location        TEXT,
    notes           TEXT,
    logged_at       TEXT    NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_payment_order     ON payment_logs(order_id);
CREATE INDEX IF NOT EXISTS idx_payment_time      ON payment_logs(logged_at);
CREATE INDEX IF NOT EXISTS idx_logistics_order   ON logistics_logs(order_id);
CREATE INDEX IF NOT EXISTS idx_logistics_time    ON logistics_logs(logged_at);
CREATE INDEX IF NOT EXISTS idx_logistics_track   ON logistics_logs(tracking_number);
"""


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _load_seed_data() -> dict:
    """Load seed data from the JSON file shipped alongside the app."""
    seed_path = Path(__file__).resolve().parent.parent / "data" / "seed.json"
    with open(seed_path, encoding="utf-8") as f:
        return json.load(f)


def init_db() -> None:
    """Create tables (if missing) and seed with sample data on first run."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        seed = _load_seed_data()
        _seed_if_empty(conn, seed)
        _seed_logs_if_empty(conn, seed)
        conn.commit()
        log.info("Database initialised successfully.")
    finally:
        conn.close()


def _table_has_rows(conn: sqlite3.Connection, table: str) -> bool:
    """Return True if *table* contains at least one row.

    NOTE: *table* is never user-supplied — only called with hardcoded names.
    """
    row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()  # noqa: S608
    return row is not None


# ---------------------------------------------------------------------------
# Seed helpers — data is loaded from data/seed.json
# ---------------------------------------------------------------------------

def _seed_if_empty(conn: sqlite3.Connection, seed: dict) -> None:
    """Populate core tables (users, orders, complaints) if they are empty."""
    if _table_has_rows(conn, "users"):
        return

    users = seed["users"]
    conn.executemany(
        "INSERT INTO users"
        " (name, email, phone, address_line, city, state, zip_code, country,"
        "  created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        users,
    )
    log.info("Seeded %d users.", len(users))

    orders = seed["orders"]
    conn.executemany(
        "INSERT INTO orders"
        " (user_id, item, quantity, unit_price, total_amount, status,"
        "  payment_method, shipping_address, tracking_number, ordered_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        orders,
    )
    log.info("Seeded %d orders.", len(orders))

    complaints = seed["complaints"]
    conn.executemany(
        "INSERT INTO complaints"
        " (user_id, order_id, category, priority, status, subject, details,"
        "  resolution, assigned_to, created_at, resolved_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        complaints,
    )
    log.info("Seeded %d complaints.", len(complaints))


def _seed_logs_if_empty(conn: sqlite3.Connection, seed: dict) -> None:
    """Populate event-log tables (payment, logistics) if empty."""
    if _table_has_rows(conn, "payment_logs"):
        return

    payments = seed["payment_logs"]
    conn.executemany(
        "INSERT INTO payment_logs"
        " (order_id, transaction_id, event_type, amount, currency, gateway,"
        "  status, error_message, logged_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        payments,
    )
    log.info("Seeded %d payment logs.", len(payments))

    logistics = seed["logistics_logs"]
    conn.executemany(
        "INSERT INTO logistics_logs"
        " (order_id, tracking_number, carrier, event_type, location, notes,"
        "  logged_at)"
        " VALUES (?,?,?,?,?,?,?)",
        logistics,
    )
    log.info("Seeded %d logistics logs.", len(logistics))
