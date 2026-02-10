"""Repository – read-only query functions for MCP tools, resources, and prompts.

Every public function is decorated with ``_with_conn`` so that callers never
need to manage database connections.  All results are plain ``dict`` /
``list[dict]`` so they serialise to JSON without extra work.
"""

from __future__ import annotations

import functools
from typing import Any

from .db import fetch_all, fetch_one, get_connection


# ── helpers ───────────────────────────────────────────────────────────────

def _with_conn(fn):
    """Decorator that opens / closes a connection around *fn(conn, ...)*."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        conn = get_connection()
        try:
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()
    return wrapper


# ═══════════════════════════════════════════════════════════════════════════
# Users
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def list_users(conn) -> list[dict[str, Any]]:
    """List all users."""
    return fetch_all(conn, "SELECT * FROM users ORDER BY id")


@_with_conn
def get_user_by_id(conn, user_id: int) -> dict[str, Any] | None:
    """Retrieve a single user by id."""
    return fetch_one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))


@_with_conn
def search_users(conn, keyword: str) -> list[dict[str, Any]]:
    """Search users by name or email (case-insensitive substring match)."""
    like = f"%{keyword}%"
    return fetch_all(
        conn,
        "SELECT * FROM users WHERE name LIKE ? OR email LIKE ? ORDER BY id",
        (like, like),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Orders
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def list_orders(
    conn,
    *,
    user_id: int | None = None,
    status: str | None = None,
    payment_method: str | None = None,
) -> list[dict[str, Any]]:
    """List orders with optional filters."""
    q = "SELECT * FROM orders WHERE 1=1"
    p: list[object] = []
    if user_id is not None:
        q += " AND user_id = ?"
        p.append(user_id)
    if status:
        q += " AND status = ?"
        p.append(status)
    if payment_method:
        q += " AND payment_method = ?"
        p.append(payment_method)
    q += " ORDER BY ordered_at DESC"
    return fetch_all(conn, q, p)


@_with_conn
def get_order_by_id(conn, order_id: int) -> dict[str, Any] | None:
    """Get full details of a single order."""
    return fetch_one(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))


@_with_conn
def get_orders_by_date_range(conn, start: str, end: str) -> list[dict[str, Any]]:
    """Return orders placed between *start* and *end* (ISO-8601 strings, inclusive)."""
    return fetch_all(
        conn,
        "SELECT * FROM orders WHERE ordered_at >= ? AND ordered_at <= ? ORDER BY ordered_at",
        (start, end),
    )


@_with_conn
def get_order_by_tracking(conn, tracking_number: str) -> dict[str, Any] | None:
    """Look up an order by its tracking number."""
    return fetch_one(conn, "SELECT * FROM orders WHERE tracking_number = ?", (tracking_number,))


# ═══════════════════════════════════════════════════════════════════════════
# Complaints
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def list_complaints(
    conn,
    *,
    user_id: int | None = None,
    status: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
) -> list[dict[str, Any]]:
    """List complaints with rich filtering."""
    q = "SELECT * FROM complaints WHERE 1=1"
    p: list[object] = []
    if user_id is not None:
        q += " AND user_id = ?"
        p.append(user_id)
    if status:
        q += " AND status = ?"
        p.append(status)
    if category:
        q += " AND category = ?"
        p.append(category)
    if priority:
        q += " AND priority = ?"
        p.append(priority)
    if assigned_to:
        q += " AND assigned_to = ?"
        p.append(assigned_to)
    q += " ORDER BY created_at DESC"
    return fetch_all(conn, q, p)


@_with_conn
def get_complaint_by_id(conn, complaint_id: int) -> dict[str, Any] | None:
    """Get full details of a single complaint."""
    return fetch_one(conn, "SELECT * FROM complaints WHERE id = ?", (complaint_id,))


@_with_conn
def get_complaints_for_order(conn, order_id: int) -> list[dict[str, Any]]:
    """All complaints linked to a specific order – useful for RCA."""
    return fetch_all(
        conn,
        "SELECT * FROM complaints WHERE order_id = ? ORDER BY created_at",
        (order_id,),
    )


@_with_conn
def search_complaints(conn, keyword: str) -> list[dict[str, Any]]:
    """Full-text search across complaint subject and details."""
    like = f"%{keyword}%"
    return fetch_all(
        conn,
        "SELECT * FROM complaints WHERE subject LIKE ? OR details LIKE ? ORDER BY created_at DESC",
        (like, like),
    )


@_with_conn
def get_high_priority_open_complaints(conn) -> list[dict[str, Any]]:
    """Return open/investigating complaints with high or critical priority."""
    return fetch_all(
        conn,
        "SELECT * FROM complaints WHERE priority IN ('high','critical') AND status IN ('open','investigating') ORDER BY priority DESC, created_at",
        (),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Aggregations / Analytics  (agent RCA helpers)
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_user_summary(conn, user_id: int) -> dict[str, Any] | None:
    """Aggregated profile: total orders, spend, complaints, tier, active."""
    user = fetch_one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        return None
    order_stats = fetch_one(
        conn,
        "SELECT COUNT(*) AS total_orders, COALESCE(SUM(total_amount),0) AS total_spent, "
        "COUNT(CASE WHEN status='delivered' THEN 1 END) AS delivered, "
        "COUNT(CASE WHEN status='returned' THEN 1 END) AS returned, "
        "COUNT(CASE WHEN status='cancelled' THEN 1 END) AS cancelled "
        "FROM orders WHERE user_id = ?",
        (user_id,),
    )
    complaint_stats = fetch_one(
        conn,
        "SELECT COUNT(*) AS total_complaints, "
        "COUNT(CASE WHEN status IN ('open','investigating','waiting_customer') THEN 1 END) AS open_complaints, "
        "COUNT(CASE WHEN priority IN ('high','critical') THEN 1 END) AS high_priority "
        "FROM complaints WHERE user_id = ?",
        (user_id,),
    )
    return {**dict(user), **(order_stats or {}), **(complaint_stats or {})}


@_with_conn
def get_complaint_statistics(conn) -> dict[str, Any]:
    """System-wide complaint breakdown by category, priority & status."""
    by_category = fetch_all(conn, "SELECT category, COUNT(*) AS cnt FROM complaints GROUP BY category ORDER BY cnt DESC", ())
    by_priority = fetch_all(conn, "SELECT priority, COUNT(*) AS cnt FROM complaints GROUP BY priority ORDER BY cnt DESC", ())
    by_status   = fetch_all(conn, "SELECT status, COUNT(*) AS cnt FROM complaints GROUP BY status ORDER BY cnt DESC", ())
    by_agent    = fetch_all(conn, "SELECT assigned_to, COUNT(*) AS cnt FROM complaints GROUP BY assigned_to ORDER BY cnt DESC", ())
    return {
        "by_category": by_category,
        "by_priority": by_priority,
        "by_status":   by_status,
        "by_agent":    by_agent,
    }


@_with_conn
def get_order_statistics(conn) -> dict[str, Any]:
    """System-wide order breakdown by status and payment method."""
    by_status  = fetch_all(conn, "SELECT status, COUNT(*) AS cnt, ROUND(SUM(total_amount),2) AS total FROM orders GROUP BY status ORDER BY cnt DESC", ())
    by_payment = fetch_all(conn, "SELECT payment_method, COUNT(*) AS cnt, ROUND(SUM(total_amount),2) AS total FROM orders GROUP BY payment_method ORDER BY cnt DESC", ())
    return {"by_status": by_status, "by_payment": by_payment}


@_with_conn
def correlate_user_issues(conn, user_id: int) -> list[dict[str, Any]]:
    """Join orders ↔ complaints for a user – the go-to RCA view."""
    return fetch_all(
        conn,
        """
        SELECT
            c.id          AS complaint_id,
            c.subject     AS complaint_subject,
            c.category    AS complaint_category,
            c.priority    AS complaint_priority,
            c.status      AS complaint_status,
            c.details     AS complaint_details,
            c.resolution  AS complaint_resolution,
            c.assigned_to,
            c.created_at  AS complaint_created_at,
            o.id          AS order_id,
            o.item        AS order_item,
            o.total_amount AS order_total,
            o.status      AS order_status,
            o.tracking_number
        FROM complaints c
        LEFT JOIN orders o ON c.order_id = o.id
        WHERE c.user_id = ?
        ORDER BY c.created_at DESC
        """,
        (user_id,),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Event Logs  (inventory, payments, logistics)
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_payment_logs(
    conn,
    *,
    order_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Payment transaction logs, optionally filtered by order and/or time window (ISO-8601)."""
    q = "SELECT * FROM payment_logs WHERE 1=1"
    p: list[object] = []
    if order_id is not None:
        q += " AND order_id = ?"
        p.append(order_id)
    if start:
        q += " AND logged_at >= ?"
        p.append(start)
    if end:
        q += " AND logged_at <= ?"
        p.append(end)
    q += " ORDER BY logged_at"
    return fetch_all(conn, q, p)


@_with_conn
def get_logistics_logs(
    conn,
    *,
    order_id: int | None = None,
    tracking_number: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Logistics/shipping event logs, optionally filtered by order, tracking number, and/or time window."""
    q = "SELECT * FROM logistics_logs WHERE 1=1"
    p: list[object] = []
    if order_id is not None:
        q += " AND order_id = ?"
        p.append(order_id)
    if tracking_number:
        q += " AND tracking_number = ?"
        p.append(tracking_number)
    if start:
        q += " AND logged_at >= ?"
        p.append(start)
    if end:
        q += " AND logged_at <= ?"
        p.append(end)
    q += " ORDER BY logged_at"
    return fetch_all(conn, q, p)


@_with_conn
def get_complaint_context_logs(conn, complaint_id: int, window_hours: int = 48) -> dict[str, Any] | None:
    """Fetch a complaint and return all correlating logs from inventory, payments,
    and logistics within ±window_hours of the complaint creation time.

    This is the primary enrichment tool for root-cause analysis.  It lets the
    agent see what was happening across inventory, finance, and logistics
    domains around the time a complaint was filed.
    """
    complaint = fetch_one(conn, "SELECT * FROM complaints WHERE id = ?", (complaint_id,))
    if not complaint:
        return None

    created = complaint["created_at"]
    order_id = complaint.get("order_id")
    wh = int(window_hours)

    result: dict = {
        "complaint": dict(complaint),
        "window_hours": wh,
        "payment_logs": [],
        "logistics_logs": [],
        "order": None,
        "user": None,
    }

    user = fetch_one(conn, "SELECT * FROM users WHERE id = ?", (complaint["user_id"],))
    result["user"] = dict(user) if user else None

    if order_id:
        order = fetch_one(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
        result["order"] = dict(order) if order else None

        result["payment_logs"] = fetch_all(
            conn,
            "SELECT * FROM payment_logs WHERE order_id = ? ORDER BY logged_at",
            (order_id,),
        )

        result["logistics_logs"] = fetch_all(
            conn,
            "SELECT * FROM logistics_logs WHERE order_id = ? ORDER BY logged_at",
            (order_id,),
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Revenue & Business Intelligence
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_revenue_by_city(conn) -> list[dict[str, Any]]:
    """Revenue breakdown grouped by user city (from shipping addresses)."""
    return fetch_all(
        conn,
        """
        SELECT u.city,
               COUNT(o.id) AS order_count,
               ROUND(SUM(o.total_amount), 2) AS total_revenue,
               ROUND(AVG(o.total_amount), 2) AS avg_order_value
        FROM orders o
        JOIN users u ON o.user_id = u.id
        GROUP BY u.city
        ORDER BY total_revenue DESC
        """,
        (),
    )


@_with_conn
def get_top_customers(conn, *, limit: int = 10) -> list[dict[str, Any]]:
    """Top customers ranked by total spend."""
    return fetch_all(
        conn,
        """
        SELECT u.id AS user_id, u.name, u.email, u.city,
               COUNT(o.id) AS order_count,
               ROUND(SUM(o.total_amount), 2) AS total_spent,
               ROUND(AVG(o.total_amount), 2) AS avg_order_value,
               MAX(o.ordered_at)              AS last_order_at
        FROM users u
        JOIN orders o ON o.user_id = u.id
        GROUP BY u.id
        ORDER BY total_spent DESC
        LIMIT ?
        """,
        (limit,),
    )


@_with_conn
def get_user_lifetime_value(conn, user_id: int) -> dict[str, Any] | None:
    """Compute a single user's lifetime value metrics (spend, frequency, complaints)."""
    row = fetch_one(
        conn,
        """
        SELECT u.id, u.name, u.email, u.city, u.state,
               u.created_at AS member_since,
               COUNT(o.id)                                          AS total_orders,
               ROUND(COALESCE(SUM(o.total_amount), 0), 2)          AS total_spent,
               ROUND(COALESCE(AVG(o.total_amount), 0), 2)          AS avg_order_value,
               MIN(o.ordered_at)                                    AS first_order,
               MAX(o.ordered_at)                                    AS last_order,
               (SELECT COUNT(*) FROM complaints c WHERE c.user_id = u.id)
                                                                    AS total_complaints,
               (SELECT COUNT(*) FROM complaints c
                 WHERE c.user_id = u.id
                   AND c.status IN ('open','investigating','waiting_customer'))
                                                                    AS open_complaints
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.id
        WHERE u.id = ?
        GROUP BY u.id
        """,
        (user_id,),
    )
    return row


# ═══════════════════════════════════════════════════════════════════════════
# Order Fulfilment & Delivery
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_order_fulfillment_timeline(conn, order_id: int) -> dict[str, Any] | None:
    """Return a chronological timeline combining order status, payments, and logistics."""
    order = fetch_one(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
    if not order:
        return None

    payments = fetch_all(
        conn,
        "SELECT id, transaction_id, event_type, amount, currency, gateway, status, error_message, logged_at "
        "FROM payment_logs WHERE order_id = ? ORDER BY logged_at",
        (order_id,),
    )
    logistics = fetch_all(
        conn,
        "SELECT id, tracking_number, carrier, event_type, location, notes, logged_at "
        "FROM logistics_logs WHERE order_id = ? ORDER BY logged_at",
        (order_id,),
    )
    complaints = fetch_all(
        conn,
        "SELECT id, category, priority, status, subject, created_at "
        "FROM complaints WHERE order_id = ? ORDER BY created_at",
        (order_id,),
    )
    return {
        "order": dict(order),
        "payment_events": payments,
        "logistics_events": logistics,
        "complaints": complaints,
    }


@_with_conn
def get_active_shipments(conn) -> list[dict[str, Any]]:
    """Return all orders currently in 'shipped' status with their latest logistics event."""
    return fetch_all(
        conn,
        """
        SELECT o.id AS order_id, o.user_id, o.item, o.tracking_number,
               o.shipping_address,
               u.name AS customer_name, u.phone AS customer_phone,
               ll.carrier, ll.event_type AS latest_event, ll.location AS latest_location,
               ll.logged_at AS latest_event_at,
               dispatch.dispatched_at
        FROM orders o
        JOIN users u ON u.id = o.user_id
        LEFT JOIN logistics_logs ll ON ll.order_id = o.id
            AND ll.logged_at = (
                SELECT MAX(ll2.logged_at) FROM logistics_logs ll2 WHERE ll2.order_id = o.id
            )
        LEFT JOIN (
            SELECT order_id, MIN(logged_at) AS dispatched_at
            FROM logistics_logs
            WHERE event_type = 'dispatched'
            GROUP BY order_id
        ) dispatch ON dispatch.order_id = o.id
        WHERE o.status = 'shipped'
        ORDER BY dispatch.dispatched_at DESC
        """,
        (),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Complaint Analytics
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_complaint_resolution_time_stats(conn) -> dict[str, Any]:
    """Statistics on complaint resolution times (in hours) for resolved/closed complaints."""
    rows = fetch_all(
        conn,
        """
        SELECT category, priority,
               ROUND(
                   (julianday(resolved_at) - julianday(created_at)) * 24, 1
               ) AS resolution_hours
        FROM complaints
        WHERE resolved_at IS NOT NULL
        ORDER BY resolution_hours DESC
        """,
        (),
    )
    overall = fetch_one(
        conn,
        """
        SELECT COUNT(*)                                                          AS resolved_count,
               ROUND(AVG((julianday(resolved_at) - julianday(created_at)) * 24), 1) AS avg_hours,
               ROUND(MIN((julianday(resolved_at) - julianday(created_at)) * 24), 1) AS min_hours,
               ROUND(MAX((julianday(resolved_at) - julianday(created_at)) * 24), 1) AS max_hours
        FROM complaints
        WHERE resolved_at IS NOT NULL
        """,
        (),
    )
    by_priority = fetch_all(
        conn,
        """
        SELECT priority,
               COUNT(*) AS cnt,
               ROUND(AVG((julianday(resolved_at) - julianday(created_at)) * 24), 1) AS avg_hours
        FROM complaints
        WHERE resolved_at IS NOT NULL
        GROUP BY priority
        ORDER BY avg_hours DESC
        """,
        (),
    )
    return {
        "overall": overall or {},
        "by_priority": by_priority,
        "details": rows,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Payment Analytics
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_payment_failure_rate(conn) -> dict[str, Any]:
    """Payment success/failure rate breakdown by gateway."""
    by_gateway = fetch_all(
        conn,
        """
        SELECT gateway,
               COUNT(*)                                                    AS total_events,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)        AS success_count,
               SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END)        AS failure_count,
               ROUND(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) * 100.0
                     / COUNT(*), 2)                                        AS failure_pct
        FROM payment_logs
        GROUP BY gateway
        ORDER BY failure_pct DESC
        """,
        (),
    )
    overall = fetch_one(
        conn,
        """
        SELECT COUNT(*)                                                    AS total_events,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)        AS success_count,
               SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END)        AS failure_count,
               ROUND(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) * 100.0
                     / COUNT(*), 2)                                        AS failure_pct
        FROM payment_logs
        """,
        (),
    )
    return {"overall": overall or {}, "by_gateway": by_gateway}


@_with_conn
def get_payment_summary_by_method(conn) -> list[dict[str, Any]]:
    """Total revenue and order count grouped by payment method."""
    return fetch_all(
        conn,
        """
        SELECT payment_method,
               COUNT(*) AS order_count,
               ROUND(SUM(total_amount), 2) AS total_revenue,
               ROUND(AVG(total_amount), 2) AS avg_order_value
        FROM orders
        GROUP BY payment_method
        ORDER BY total_revenue DESC
        """,
        (),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Carrier & Logistics Performance
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_carrier_performance(conn) -> list[dict[str, Any]]:
    """Per-carrier delivery performance: events count, unique orders, and average
    time from first event (label_created) to last event (delivered) in hours."""
    return fetch_all(
        conn,
        """
        SELECT ll.carrier,
               COUNT(*)                    AS total_events,
               COUNT(DISTINCT ll.order_id) AS orders_handled,
               ROUND(AVG(
                   CASE WHEN d.delivered_at IS NOT NULL AND d.first_event IS NOT NULL
                        THEN (julianday(d.delivered_at) - julianday(d.first_event)) * 24
                   END
               ), 1) AS avg_delivery_hours
        FROM logistics_logs ll
        LEFT JOIN (
            SELECT ll2.order_id,
                   MIN(ll2.logged_at) AS first_event,
                   MAX(CASE WHEN ll2.event_type = 'delivered' THEN ll2.logged_at END) AS delivered_at
            FROM logistics_logs ll2
            GROUP BY ll2.order_id
        ) d ON d.order_id = ll.order_id
        GROUP BY ll.carrier
        ORDER BY orders_handled DESC
        """,
        (),
    )


@_with_conn
def get_order_delivery_time(conn, order_id: int) -> dict[str, Any] | None:
    """Calculate the delivery time for a specific order in hours.

    Derives shipped_at and delivered_at from logistics_logs event types
    ('dispatched' and 'delivered' respectively).
    """
    return fetch_one(
        conn,
        """
        SELECT o.id AS order_id, o.item, o.tracking_number,
               o.ordered_at,
               dispatch.shipped_at,
               delivery.delivered_at,
               ROUND((julianday(dispatch.shipped_at) - julianday(o.ordered_at)) * 24, 1) AS processing_hours,
               ROUND((julianday(delivery.delivered_at) - julianday(dispatch.shipped_at)) * 24, 1) AS shipping_hours,
               ROUND((julianday(delivery.delivered_at) - julianday(o.ordered_at)) * 24, 1) AS total_hours
        FROM orders o
        LEFT JOIN (
            SELECT order_id, MIN(logged_at) AS shipped_at
            FROM logistics_logs
            WHERE event_type = 'dispatched'
            GROUP BY order_id
        ) dispatch ON dispatch.order_id = o.id
        LEFT JOIN (
            SELECT order_id, MAX(logged_at) AS delivered_at
            FROM logistics_logs
            WHERE event_type = 'delivered'
            GROUP BY order_id
        ) delivery ON delivery.order_id = o.id
        WHERE o.id = ?
        """,
        (order_id,),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Cross-domain / Dashboard
# ═══════════════════════════════════════════════════════════════════════════

@_with_conn
def get_dashboard_summary(conn) -> dict[str, Any]:
    """High-level dashboard metrics across all domains."""
    users = fetch_one(conn, """
        SELECT COUNT(*) AS total
        FROM users
    """, ())
    orders = fetch_one(conn, """
        SELECT COUNT(*) AS total,
               ROUND(SUM(total_amount), 2) AS total_revenue,
               ROUND(AVG(total_amount), 2) AS avg_order_value,
               SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
               SUM(CASE WHEN status = 'shipped' THEN 1 ELSE 0 END) AS in_transit,
               SUM(CASE WHEN status IN ('pending','processing') THEN 1 ELSE 0 END) AS pending
        FROM orders
    """, ())
    complaints = fetch_one(conn, """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status IN ('open','investigating','waiting_customer') THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN priority IN ('high','critical') THEN 1 ELSE 0 END) AS high_priority
        FROM complaints
    """, ())
    return {
        "users": users or {},
        "orders": orders or {},
        "complaints": complaints or {},
    }
