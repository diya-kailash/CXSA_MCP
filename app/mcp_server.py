"""MCP Server – exposes the SQLite-backed context service via **Tools**, **Resources** and **Prompts**.

Transports:
  stdio:  python -m app.mcp_server                          (default)
  http:   python -m app.mcp_server --transport http          (port 8001)
          python -m app.mcp_server --transport http --port 9000
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)

from .db import init_db
from . import repository as repo

server = Server("SampleMCPServer", "2.0.0", "MCP server exposing Indian e-commerce customer/order/complaint data, analytics and RCA tools.")


# ═══════════════════════════════════════════════════════════════════════════
# TOOLS  (30 tools – filtering, look-ups, search, stats, RCA, analytics)
# ═══════════════════════════════════════════════════════════════════════════

TOOLS: list[Tool] = [
    # ── Users ─────────────────────────────────────────────────────────────
    Tool(
        name="list_users",
        description="List all users.",
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_user_by_id",
        description="Get full details for a single user by their id.",
        inputSchema={
            "type": "object",
            "properties": {"user_id": {"type": "integer", "minimum": 1}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="search_users",
        description="Search users by name or email (case-insensitive substring match).",
        inputSchema={
            "type": "object",
            "properties": {"keyword": {"type": "string", "minLength": 1}},
            "required": ["keyword"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_user_summary",
        description="Aggregated profile for a user: total orders, total spent, delivered/returned/cancelled counts, total complaints, open complaints and high-priority count.",
        inputSchema={
            "type": "object",
            "properties": {"user_id": {"type": "integer", "minimum": 1}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    ),
    # ── Orders ────────────────────────────────────────────────────────────
    Tool(
        name="list_orders",
        description="List orders. Optionally filter by user_id, status (pending/processing/shipped/delivered/cancelled/returned) and/or payment_method.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "minimum": 1},
                "status": {"type": "string", "enum": ["pending", "processing", "shipped", "delivered", "cancelled", "returned"]},
                "payment_method": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_order_by_id",
        description="Get full details of a single order including tracking info and timestamps.",
        inputSchema={
            "type": "object",
            "properties": {"order_id": {"type": "integer", "minimum": 1}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_orders_by_date_range",
        description="Retrieve all orders placed within a date range (ISO-8601 strings).",
        inputSchema={
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "ISO-8601 start datetime"},
                "end": {"type": "string", "description": "ISO-8601 end datetime"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_order_by_tracking",
        description="Look up an order by its tracking number.",
        inputSchema={
            "type": "object",
            "properties": {"tracking_number": {"type": "string", "minLength": 1}},
            "required": ["tracking_number"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_order_statistics",
        description="System-wide order breakdown by status and payment method (counts and totals).",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    # ── Complaints ────────────────────────────────────────────────────────
    Tool(
        name="list_complaints",
        description="List complaints. Filter by user_id, status, category, priority and/or assigned_to.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "minimum": 1},
                "status": {"type": "string", "enum": ["open", "investigating", "waiting_customer", "resolved", "closed"]},
                "category": {"type": "string", "enum": ["delivery", "billing", "product", "service", "account", "other"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "assigned_to": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_complaint_by_id",
        description="Get full details of a single complaint including resolution and assignment.",
        inputSchema={
            "type": "object",
            "properties": {"complaint_id": {"type": "integer", "minimum": 1}},
            "required": ["complaint_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="search_complaints",
        description="Full-text search across complaint subject and details fields.",
        inputSchema={
            "type": "object",
            "properties": {"keyword": {"type": "string", "minLength": 1}},
            "required": ["keyword"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_high_priority_open_complaints",
        description="Retrieve all open or investigating complaints with high or critical priority – the urgent queue.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_complaint_statistics",
        description="System-wide complaint analytics broken down by category, priority, status and assigned agent.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    # ── RCA / Cross-entity ────────────────────────────────────────────────
    Tool(
        name="get_complaints_for_order",
        description="All complaints linked to a specific order – essential for root-cause analysis.",
        inputSchema={
            "type": "object",
            "properties": {"order_id": {"type": "integer", "minimum": 1}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="correlate_user_issues",
        description="Join orders and complaints for a user in a single view – the primary root-cause analysis tool.",
        inputSchema={
            "type": "object",
            "properties": {"user_id": {"type": "integer", "minimum": 1}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    ),    # ── Event Logs (payments, logistics) ─────────────────────────────────
    Tool(
        name="get_payment_logs",
        description=(
            "Query payment transaction logs (authorized, captured, refunded, voided, "
            "failed, chargeback). Filter by order_id and/or time window. Essential for "
            "billing complaints \u2013 reveals double charges, failed refunds, ghost charges, "
            "and payment gateway errors."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "minimum": 1},
                "start":    {"type": "string", "description": "ISO-8601 start datetime"},
                "end":      {"type": "string", "description": "ISO-8601 end datetime"},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_logistics_logs",
        description=(
            "Query shipping/logistics event logs (label_created, picked, packed, dispatched, "
            "in_transit, delivered, delivery_failed, held_at_facility, etc.). Filter by "
            "order_id, tracking_number, and/or time window. Critical for delivery complaints "
            "\u2013 shows delays, damage in transit, stuck shipments, and carrier issues."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "order_id":        {"type": "integer", "minimum": 1},
                "tracking_number": {"type": "string"},
                "start":           {"type": "string", "description": "ISO-8601 start datetime"},
                "end":             {"type": "string", "description": "ISO-8601 end datetime"},
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_complaint_context_logs",
        description=(
            "THE PRIMARY ENRICHMENT TOOL.  Given a complaint_id, automatically fetches "
            "the complaint, its linked order, the customer profile, AND all correlated "
            "logs from payments and logistics around the complaint\u2019s creation "
            "time (\u00b1window_hours, default 48h).  Use this as the FIRST step in any deep "
            "root-cause investigation, then drill into specific domains with the individual "
            "log tools if needed. Returns: complaint, order, user, "
            "payment_logs, logistics_logs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "complaint_id":  {"type": "integer", "minimum": 1},
                "window_hours":  {"type": "integer", "minimum": 1, "maximum": 720, "description": "Hours before/after complaint to search (default 48)"},
            },
            "required": ["complaint_id"],
            "additionalProperties": False,
        },
    ),
    # ── Revenue & Business Intelligence ───────────────────────────────────
    Tool(
        name="get_revenue_by_city",
        description=(
            "Revenue breakdown by customer city. Shows order count, total revenue, "
            "and average order value per city. Useful for regional performance analysis."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_top_customers",
        description=(
            "Top customers ranked by total spend. Returns user details, order count, "
            "total spent, average order value and last order date. Configurable limit."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Number of top customers to return (default: 10)",
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_user_lifetime_value",
        description=(
            "Compute lifetime value metrics for a single user: total orders, total spend, "
            "average order value, first/last order dates, complaint counts, membership "
            "duration. Essential for churn risk and VIP identification."
        ),
        inputSchema={
            "type": "object",
            "properties": {"user_id": {"type": "integer", "minimum": 1}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_dashboard_summary",
        description=(
            "High-level dashboard with key metrics across all domains: user count, "
            "order pipeline (total, revenue, delivered, in-transit, "
            "pending), and complaint health (total, open, high-priority)."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    # ── Order Fulfilment & Delivery ───────────────────────────────────────
    Tool(
        name="get_order_fulfillment_timeline",
        description=(
            "Full chronological timeline for a single order combining order details, "
            "payment events, logistics events, and any linked complaints. The definitive "
            "tool for understanding what happened to an order end-to-end."
        ),
        inputSchema={
            "type": "object",
            "properties": {"order_id": {"type": "integer", "minimum": 1}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_active_shipments",
        description=(
            "All orders currently in 'shipped' status with their latest logistics event, "
            "carrier, location and customer contact details. Use for shipment monitoring."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_order_delivery_time",
        description=(
            "Calculate processing time, shipping time and total delivery time (in hours) "
            "for a specific order. Returns None values for undelivered orders."
        ),
        inputSchema={
            "type": "object",
            "properties": {"order_id": {"type": "integer", "minimum": 1}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    # ── Complaint Analytics ───────────────────────────────────────────────
    Tool(
        name="get_complaint_resolution_time_stats",
        description=(
            "Resolution-time statistics for complaints: min, max, average and median "
            "hours from creation to resolution. Helps measure support responsiveness."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    # ── Payment Analytics ─────────────────────────────────────────────────
    Tool(
        name="get_payment_failure_rate",
        description=(
            "Payment success/failure rate breakdown by gateway (Razorpay, Paytm, PhonePe, "
            "Cashfree, BillDesk, CCAvenue). Shows total events, success/failure counts "
            "and failure percentage. Use to identify unreliable payment gateways."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_payment_summary_by_method",
        description=(
            "Revenue and order count grouped by payment method (UPI, net banking, wallet, "
            "COD, EMI, credit/debit card). Useful for understanding payment preferences."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    # ── Carrier Performance ───────────────────────────────────────────────
    Tool(
        name="get_carrier_performance",
        description=(
            "Per-carrier delivery performance for Indian carriers (BlueDart, Delhivery, "
            "DTDC, Ekart, XpressBees, India Post, Shadowfax, Ecom Express). Shows "
            "event counts, orders handled and average delivery time in hours."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
]

# Build a dispatcher map:  tool_name -> callable
_TOOL_DISPATCH: dict[str, Any] = {
    "list_users":                        lambda a: repo.list_users(),
    "get_user_by_id":                    lambda a: repo.get_user_by_id(a["user_id"]),
    "search_users":                      lambda a: repo.search_users(a["keyword"]),
    "get_user_summary":                  lambda a: repo.get_user_summary(a["user_id"]),
    "list_orders":                       lambda a: repo.list_orders(user_id=a.get("user_id"), status=a.get("status"), payment_method=a.get("payment_method")),
    "get_order_by_id":                   lambda a: repo.get_order_by_id(a["order_id"]),
    "get_orders_by_date_range":          lambda a: repo.get_orders_by_date_range(a["start"], a["end"]),
    "get_order_by_tracking":             lambda a: repo.get_order_by_tracking(a["tracking_number"]),
    "get_order_statistics":              lambda a: repo.get_order_statistics(),
    "list_complaints":                   lambda a: repo.list_complaints(user_id=a.get("user_id"), status=a.get("status"), category=a.get("category"), priority=a.get("priority"), assigned_to=a.get("assigned_to")),
    "get_complaint_by_id":               lambda a: repo.get_complaint_by_id(a["complaint_id"]),
    "search_complaints":                 lambda a: repo.search_complaints(a["keyword"]),
    "get_high_priority_open_complaints": lambda a: repo.get_high_priority_open_complaints(),
    "get_complaint_statistics":          lambda a: repo.get_complaint_statistics(),
    "get_complaints_for_order":          lambda a: repo.get_complaints_for_order(a["order_id"]),
    "correlate_user_issues":             lambda a: repo.correlate_user_issues(a["user_id"]),
    "get_payment_logs":                  lambda a: repo.get_payment_logs(order_id=a.get("order_id"), start=a.get("start"), end=a.get("end")),
    "get_logistics_logs":                lambda a: repo.get_logistics_logs(order_id=a.get("order_id"), tracking_number=a.get("tracking_number"), start=a.get("start"), end=a.get("end")),
    "get_complaint_context_logs":         lambda a: repo.get_complaint_context_logs(a["complaint_id"], window_hours=a.get("window_hours", 48)),
    # Revenue & BI
    "get_revenue_by_city":                lambda a: repo.get_revenue_by_city(),
    "get_top_customers":                  lambda a: repo.get_top_customers(limit=a.get("limit", 10)),
    "get_user_lifetime_value":            lambda a: repo.get_user_lifetime_value(a["user_id"]),
    "get_dashboard_summary":              lambda a: repo.get_dashboard_summary(),
    # Order Fulfilment
    "get_order_fulfillment_timeline":     lambda a: repo.get_order_fulfillment_timeline(a["order_id"]),
    "get_active_shipments":               lambda a: repo.get_active_shipments(),
    "get_order_delivery_time":            lambda a: repo.get_order_delivery_time(a["order_id"]),
    # Complaint Analytics
    "get_complaint_resolution_time_stats": lambda a: repo.get_complaint_resolution_time_stats(),
    # Payment Analytics
    "get_payment_failure_rate":           lambda a: repo.get_payment_failure_rate(),
    "get_payment_summary_by_method":      lambda a: repo.get_payment_summary_by_method(),
    # Carrier Performance
    "get_carrier_performance":            lambda a: repo.get_carrier_performance(),
}


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        raise ValueError(f"Unknown tool: {name}")
    payload = fn(arguments)
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


# ═══════════════════════════════════════════════════════════════════════════
# RESOURCES  (13 read-only data snapshots)
# ═══════════════════════════════════════════════════════════════════════════

RESOURCES: list[Resource] = [
    # Data snapshots
    Resource(uri="context://data/users",            name="All Users",            mimeType="application/json", description="Full users table snapshot."),
    Resource(uri="context://data/orders",           name="All Orders",           mimeType="application/json", description="Full orders table snapshot."),
    Resource(uri="context://data/complaints",       name="All Complaints",       mimeType="application/json", description="Full complaints table snapshot."),
    # Statistics
    Resource(uri="context://stats/orders",          name="Order Statistics",     mimeType="application/json", description="Aggregated order stats by status and payment method."),
    Resource(uri="context://stats/complaints",      name="Complaint Statistics", mimeType="application/json", description="Aggregated complaint stats by category, priority, status and agent."),
    Resource(uri="context://stats/revenue-by-city", name="Revenue by City",      mimeType="application/json", description="Revenue breakdown by customer city with order counts and averages."),
    Resource(uri="context://stats/top-customers",   name="Top Customers",        mimeType="application/json", description="Top 10 customers by total spend."),
    Resource(uri="context://stats/payment-failure",  name="Payment Failure Rate", mimeType="application/json", description="Payment success/failure rates by gateway."),
    Resource(uri="context://stats/carrier-performance", name="Carrier Performance", mimeType="application/json", description="Delivery performance metrics per carrier."),
    # Alerts
    Resource(uri="context://alerts/high-priority",  name="High-Priority Alerts", mimeType="application/json", description="Open/investigating complaints with high or critical priority."),
    # Logs
    Resource(uri="context://logs/payments",          name="All Payment Logs",     mimeType="application/json", description="Full payment transaction log snapshot."),
    Resource(uri="context://logs/logistics",         name="All Logistics Logs",   mimeType="application/json", description="Full logistics/shipping event log snapshot."),
    # Dashboard
    Resource(uri="context://dashboard/summary",     name="Dashboard Summary",    mimeType="application/json", description="High-level dashboard metrics across users, orders, and complaints."),
]

_RESOURCE_DISPATCH: dict[str, Any] = {
    "context://data/users":                lambda: repo.list_users(),
    "context://data/orders":               lambda: repo.list_orders(),
    "context://data/complaints":           lambda: repo.list_complaints(),
    "context://stats/orders":              lambda: repo.get_order_statistics(),
    "context://stats/complaints":          lambda: repo.get_complaint_statistics(),
    "context://stats/revenue-by-city":     lambda: repo.get_revenue_by_city(),
    "context://stats/top-customers":       lambda: repo.get_top_customers(),
    "context://stats/payment-failure":     lambda: repo.get_payment_failure_rate(),
    "context://stats/carrier-performance": lambda: repo.get_carrier_performance(),
    "context://alerts/high-priority":      lambda: repo.get_high_priority_open_complaints(),
    "context://logs/payments":             lambda: repo.get_payment_logs(),
    "context://logs/logistics":            lambda: repo.get_logistics_logs(),
    "context://dashboard/summary":         lambda: repo.get_dashboard_summary(),
}


@server.list_resources()
async def handle_list_resources() -> list[Resource]:
    return RESOURCES


@server.read_resource()
async def handle_read_resource(uri: str) -> str:
    fn = _RESOURCE_DISPATCH.get(str(uri))
    if fn is None:
        raise ValueError(f"Unknown resource: {uri}")
    return json.dumps(fn(), indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════════
# PROMPTS  (9 guided prompts for the LLM)
# ═══════════════════════════════════════════════════════════════════════════

PROMPTS: list[Prompt] = [
    Prompt(
        name="user_360_view",
        description=(
            "Build a comprehensive 360-degree view of a user: profile, order history, "
            "complaints, spending patterns and risk signals.  Use this before answering "
            "any question about a specific customer."
        ),
        arguments=[
            PromptArgument(name="user_id", description="The numeric user id to analyse.", required=True),
        ],
    ),
    Prompt(
        name="root_cause_analysis",
        description=(
            "Perform root-cause analysis on a complaint.  Correlate the complaint with "
            "its linked order, user history and similar complaints to determine the "
            "underlying issue and suggest next steps."
        ),
        arguments=[
            PromptArgument(name="complaint_id", description="Complaint id to investigate.", required=True),
        ],
    ),
    Prompt(
        name="escalation_review",
        description=(
            "Review all open high-priority and critical complaints, identify patterns, "
            "and recommend escalation or resolution actions."
        ),
        arguments=[],
    ),
    Prompt(
        name="order_investigation",
        description=(
            "Investigate an order end-to-end: payment events, logistics tracking, "
            "delivery timeline and linked complaints."
        ),
        arguments=[
            PromptArgument(name="order_id", description="Order id to investigate.", required=True),
        ],
    ),
    Prompt(
        name="system_health_overview",
        description=(
            "Produce a system-health dashboard: order pipeline status, complaint "
            "volume by category/priority, agent workload and any emerging trends."
        ),
        arguments=[],
    ),
    Prompt(
        name="deep_root_cause_analysis",
        description=(
            "Perform deep root-cause analysis on a complaint by enriching it with "
            "payment and logistics logs around the complaint timeline.  "
            "Automatically determines which data domains are most relevant based on "
            "the complaint category, then correlates timestamps across all sources "
            "to identify the root cause.  Use this for thorough investigations."
        ),
        arguments=[
            PromptArgument(name="complaint_id", description="Complaint id to investigate.", required=True),
            PromptArgument(name="window_hours", description="Hours ± around complaint time to search logs (default 48).", required=False),
        ],
    ),
    Prompt(
        name="customer_churn_risk",
        description=(
            "Assess churn risk for a customer based on their lifetime value, complaint "
            "history, open issues and recent activity.  Provides a risk score, contributing "
            "factors, and retention recommendations."
        ),
        arguments=[
            PromptArgument(name="user_id", description="User id to assess churn risk for.", required=True),
        ],
    ),
    Prompt(
        name="regional_performance_review",
        description=(
            "Analyse business performance across Indian cities: revenue distribution, "
            "order volumes, carrier reliability, and complaint hotspots.  Provides "
            "actionable insights for regional strategy."
        ),
        arguments=[],
    ),
    Prompt(
        name="payment_health_audit",
        description=(
            "Audit payment system health: gateway success/failure rates, payment method "
            "distribution, revenue by payment type, and anomaly detection.  Identifies "
            "unreliable gateways and opportunities for payment UX improvement."
        ),
        arguments=[],
    ),
]


@server.list_prompts()
async def handle_list_prompts() -> list[Prompt]:
    return PROMPTS


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, Any] | None) -> GetPromptResult:
    arguments = arguments or {}

    # ── user_360_view ─────────────────────────────────────────────────────
    if name == "user_360_view":
        uid = int(arguments["user_id"])
        summary = repo.get_user_summary(uid)
        orders = repo.list_orders(user_id=uid)
        issues = repo.correlate_user_issues(uid)
        text = (
            "You are a customer-intelligence analyst.  Using the data below, produce a "
            "comprehensive 360-degree view of this customer.  Include:\n"
            "1. Profile overview (location, account age)\n"
            "2. Order history summary (count, total spend, statuses)\n"
            "3. Complaint analysis (categories, severities, open items)\n"
            "4. Risk signals (repeated issues, high-priority open complaints, returns)\n"
            "5. Recommended next actions\n\n"
            f"### User Summary\n```json\n{json.dumps(summary, indent=2, default=str)}\n```\n\n"
            f"### Orders\n```json\n{json.dumps(orders, indent=2, default=str)}\n```\n\n"
            f"### Correlated Issues (Orders <-> Complaints)\n```json\n{json.dumps(issues, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description=f"360-degree view for user {uid}",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── root_cause_analysis ───────────────────────────────────────────────
    if name == "root_cause_analysis":
        cid = int(arguments["complaint_id"])
        complaint = repo.get_complaint_by_id(cid)
        order = repo.get_order_by_id(complaint["order_id"]) if complaint and complaint.get("order_id") else None
        user_issues = repo.correlate_user_issues(complaint["user_id"]) if complaint else []
        similar = repo.search_complaints(complaint["subject"].split()[0]) if complaint else []
        text = (
            "You are a root-cause analysis specialist.  Given the complaint and its "
            "related context, determine:\n"
            "1. What went wrong (root cause)\n"
            "2. Contributing factors\n"
            "3. Impact scope (is this affecting other users/orders?)\n"
            "4. Recommended resolution\n"
            "5. Preventive measures\n\n"
            f"### Complaint\n```json\n{json.dumps(complaint, indent=2, default=str)}\n```\n\n"
            f"### Linked Order\n```json\n{json.dumps(order, indent=2, default=str)}\n```\n\n"
            f"### User's Full Issue History\n```json\n{json.dumps(user_issues, indent=2, default=str)}\n```\n\n"
            f"### Potentially Similar Complaints\n```json\n{json.dumps(similar[:5], indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description=f"RCA for complaint {cid}",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── escalation_review ─────────────────────────────────────────────────
    if name == "escalation_review":
        urgent = repo.get_high_priority_open_complaints()
        stats = repo.get_complaint_statistics()
        text = (
            "You are an escalation manager.  Review the high-priority open complaints "
            "and complaint statistics below.  For each complaint:\n"
            "1. Assess urgency and business impact\n"
            "2. Identify patterns across complaints\n"
            "3. Recommend escalation path or immediate resolution\n"
            "4. Suggest process improvements\n\n"
            f"### Urgent Queue ({len(urgent)} items)\n```json\n{json.dumps(urgent, indent=2, default=str)}\n```\n\n"
            f"### System-Wide Complaint Statistics\n```json\n{json.dumps(stats, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description="Escalation review",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── order_investigation ───────────────────────────────────────────────
    if name == "order_investigation":
        oid = int(arguments["order_id"])
        timeline = repo.get_order_fulfillment_timeline(oid)
        if not timeline:
            raise ValueError(f"Order {oid} not found")
        order = timeline["order"]
        user = repo.get_user_by_id(order["user_id"]) if order else None
        delivery_time = repo.get_order_delivery_time(oid)
        text = (
            "You are an order-fulfilment investigator.  Analyse this order end-to-end:\n"
            "1. Payment verification (check all payment events for anomalies)\n"
            "2. Logistics timeline analysis (carrier, tracking events, delays)\n"
            "3. Delivery timeline assessment (processing, shipping, total hours)\n"
            "4. Any linked complaints and their severity\n"
            "5. Customer context and risk level\n"
            "6. Recommended actions\n\n"
            f"### Order\n```json\n{json.dumps(order, indent=2, default=str)}\n```\n\n"
            f"### Payment Events\n```json\n{json.dumps(timeline['payment_events'], indent=2, default=str)}\n```\n\n"
            f"### Logistics Events\n```json\n{json.dumps(timeline['logistics_events'], indent=2, default=str)}\n```\n\n"
            f"### Complaints on this Order\n```json\n{json.dumps(timeline['complaints'], indent=2, default=str)}\n```\n\n"
            f"### Delivery Time Metrics\n```json\n{json.dumps(delivery_time, indent=2, default=str)}\n```\n\n"
            f"### Customer Profile\n```json\n{json.dumps(user, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description=f"Investigation for order {oid}",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── system_health_overview ────────────────────────────────────────────
    if name == "system_health_overview":
        dashboard = repo.get_dashboard_summary()
        order_stats = repo.get_order_statistics()
        complaint_stats = repo.get_complaint_statistics()
        urgent = repo.get_high_priority_open_complaints()
        carrier = repo.get_carrier_performance()
        text = (
            "You are a business-intelligence analyst.  Using the data below, produce a "
            "system-health dashboard covering:\n"
            "1. Dashboard summary (users, orders, complaints at a glance)\n"
            "2. Order pipeline (pending -> processing -> shipped -> delivered / cancelled / returned)\n"
            "3. Revenue breakdown by payment method\n"
            "4. Complaint volume by category and priority\n"
            "5. Agent workload distribution\n"
            "6. Carrier performance and delivery reliability\n"
            "7. Emerging trends and risk areas\n"
            "8. Actionable recommendations\n\n"
            f"### Dashboard Summary\n```json\n{json.dumps(dashboard, indent=2, default=str)}\n```\n\n"
            f"### Order Statistics\n```json\n{json.dumps(order_stats, indent=2, default=str)}\n```\n\n"
            f"### Complaint Statistics\n```json\n{json.dumps(complaint_stats, indent=2, default=str)}\n```\n\n"
            f"### Carrier Performance\n```json\n{json.dumps(carrier, indent=2, default=str)}\n```\n\n"
            f"### High-Priority Open Complaints ({len(urgent)})\n```json\n{json.dumps(urgent, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description="System health overview",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── deep_root_cause_analysis ──────────────────────────────────────────
    if name == "deep_root_cause_analysis":
        cid = int(arguments["complaint_id"])
        wh = int(arguments.get("window_hours", 48))
        context = repo.get_complaint_context_logs(cid, window_hours=wh)
        if not context:
            raise ValueError(f"Complaint {cid} not found")

        complaint = context["complaint"]
        category = complaint.get("category", "unknown")

        # Build domain-specific guidance based on complaint category
        domain_hints = {
            "delivery":  "LOGISTICS LOGS are the primary source – look for delays, held_at_facility events, damage notes, stuck tracking.",
            "billing":   "PAYMENT LOGS are the primary source – look for duplicate captures, failed refunds, ghost charges, amount mismatches.  Cross-check order totals.",
            "product":   "LOGISTICS LOGS may reveal transit damage.  PAYMENT LOGS show refund status.  Correlate with complaint timeline.",
            "service":   "Check payment and logistics logs for systemic delays or patterns.  Focus on timeline gaps.",
            "account":   "Payment and logistics logs may reveal unauthorized activity.  Check user profile carefully.",
            "other":     "Examine all log sources equally.  Look for anomalies in any domain.",
        }
        hint = domain_hints.get(category, domain_hints["other"])

        text = (
            "You are a senior root-cause analysis specialist with access to cross-domain "
            "operational logs.  Perform a DEEP investigation of this complaint.\n\n"
            f"**Complaint Category: `{category}`**\n"
            f"**Domain Guidance:** {hint}\n\n"
            "## Investigation Steps\n"
            "1. Review the complaint details and linked order\n"
            "2. Examine the **payment logs** – look for duplicate charges, failed transactions, "
            "refund delays, or amount mismatches\n"
            "3. Examine the **logistics logs** – look for shipping delays, transit damage, "
            "stuck tracking, held_at_facility events, or delivery failures\n"
            "4. **Correlate timestamps** across all domains to find the causal chain\n"
            "5. Determine root cause, contributing factors, and impact scope\n"
            "6. Recommend resolution and preventive measures\n\n"
            "If you need more detail on any domain, use the individual log tools "
            "(get_payment_logs, get_logistics_logs) with adjusted "
            "time windows.\n\n"
            f"### Complaint\n```json\n{json.dumps(context['complaint'], indent=2, default=str)}\n```\n\n"
            f"### Linked Order\n```json\n{json.dumps(context.get('order'), indent=2, default=str)}\n```\n\n"
            f"### Customer Profile\n```json\n{json.dumps(context.get('user'), indent=2, default=str)}\n```\n\n"
            f"### Payment Logs (full order history)\n```json\n{json.dumps(context['payment_logs'], indent=2, default=str)}\n```\n\n"
            f"### Logistics Logs (full order history)\n```json\n{json.dumps(context['logistics_logs'], indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description=f"Deep RCA for complaint {cid} (category={category}, window=±{wh}h)",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── customer_churn_risk ───────────────────────────────────────────────
    if name == "customer_churn_risk":
        uid = int(arguments["user_id"])
        ltv = repo.get_user_lifetime_value(uid)
        if not ltv:
            raise ValueError(f"User {uid} not found")
        issues = repo.correlate_user_issues(uid)
        orders = repo.list_orders(user_id=uid)
        text = (
            "You are a customer retention specialist.  Assess the churn risk for this "
            "customer based on the data below.  Provide:\n"
            "1. **Risk Score** (Low / Medium / High / Critical) with justification\n"
            "2. **Key Risk Factors** (e.g., unresolved complaints, declining order frequency, "
            "high-value customer with open issues)\n"
            "3. **Customer Health Indicators** (order recency, frequency, monetary value)\n"
            "4. **Complaint Sentiment** (categories, resolutions, unresolved items)\n"
            "5. **Retention Recommendations** (specific actions to retain this customer)\n\n"
            f"### Lifetime Value Metrics\n```json\n{json.dumps(ltv, indent=2, default=str)}\n```\n\n"
            f"### Order History\n```json\n{json.dumps(orders, indent=2, default=str)}\n```\n\n"
            f"### Correlated Issues\n```json\n{json.dumps(issues, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description=f"Churn risk assessment for user {uid}",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── regional_performance_review ───────────────────────────────────────
    if name == "regional_performance_review":
        revenue = repo.get_revenue_by_city()
        carrier = repo.get_carrier_performance()
        top = repo.get_top_customers(limit=10)
        complaint_stats = repo.get_complaint_statistics()
        dashboard = repo.get_dashboard_summary()
        text = (
            "You are a regional business strategist for an Indian e-commerce company.  "
            "Analyse performance across cities and regions:\n"
            "1. **Revenue Distribution** – which cities drive the most revenue?\n"
            "2. **Order Concentration** – are we too dependent on a few cities?\n"
            "3. **Carrier Reliability** – which carriers perform best/worst in which regions?\n"
            "4. **Complaint Hotspots** – any city-level complaint patterns?\n"
            "5. **Top Customer Geography** – where are our VIP customers?\n"
            "6. **Growth Opportunities** – underserved cities with potential\n"
            "7. **Logistics Recommendations** – carrier allocation improvements\n\n"
            f"### Revenue by City\n```json\n{json.dumps(revenue, indent=2, default=str)}\n```\n\n"
            f"### Carrier Performance\n```json\n{json.dumps(carrier, indent=2, default=str)}\n```\n\n"
            f"### Top Customers\n```json\n{json.dumps(top, indent=2, default=str)}\n```\n\n"
            f"### Complaint Statistics\n```json\n{json.dumps(complaint_stats, indent=2, default=str)}\n```\n\n"
            f"### Dashboard Summary\n```json\n{json.dumps(dashboard, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description="Regional performance review (India)",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    # ── payment_health_audit ──────────────────────────────────────────────
    if name == "payment_health_audit":
        failure = repo.get_payment_failure_rate()
        by_method = repo.get_payment_summary_by_method()
        order_stats = repo.get_order_statistics()
        text = (
            "You are a payments operations analyst for an Indian e-commerce platform "
            "supporting UPI, net banking, wallets, COD, EMI, credit and debit cards "
            "across gateways like Razorpay, Paytm, PhonePe, Cashfree, BillDesk and "
            "CCAvenue.  Perform a comprehensive payment health audit:\n"
            "1. **Gateway Reliability** – success/failure rates per gateway; identify any\n"
            "   gateway with concerning failure rates\n"
            "2. **Payment Method Mix** – distribution of revenue across payment methods;\n"
            "   highlight dominant vs. underused methods\n"
            "3. **Revenue Impact** – failed payments' impact on revenue\n"
            "4. **UPI vs. Card vs. COD Analysis** – trends in digital adoption\n"
            "5. **Anomaly Detection** – unusual patterns (duplicate charges, ghost payments,\n"
            "   refund delays)\n"
            "6. **Recommendations** – gateway switching, method promotion, fraud prevention\n\n"
            f"### Payment Failure Rates by Gateway\n```json\n{json.dumps(failure, indent=2, default=str)}\n```\n\n"
            f"### Revenue by Payment Method\n```json\n{json.dumps(by_method, indent=2, default=str)}\n```\n\n"
            f"### Order Statistics (by payment method)\n```json\n{json.dumps(order_stats, indent=2, default=str)}\n```"
        )
        return GetPromptResult(
            description="Payment health audit",
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
        )

    raise ValueError(f"Unknown prompt: {name}")


# ═══════════════════════════════════════════════════════════════════════════
# Entry-point  (stdio | http)
# ═══════════════════════════════════════════════════════════════════════════

# ── stdio ─────────────────────────────────────────────────────────────────

async def _run_stdio() -> None:
    init_db()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ── http (Streamable HTTP) ────────────────────────────────────────────────

def _build_http_app():
    """Return a Starlette ASGI app serving the MCP server over Streamable HTTP."""
    from starlette.applications import Starlette
    from starlette.middleware.cors import CORSMiddleware
    from starlette.routing import Mount

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=True,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        init_db()
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        debug=False,
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
        lifespan=lifespan,
    )

    # Wrap with CORS so browser-based / cross-origin clients can connect
    starlette_app = CORSMiddleware(
        starlette_app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    return starlette_app


def _run_http(host: str, port: int) -> None:
    import uvicorn

    app = _build_http_app()
    print(f"MCP HTTP server listening on http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port)


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    transport = "stdio"
    host = "127.0.0.1"
    port = 8001

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("--transport", "-t") and i + 1 < len(args):
            transport = args[i + 1].lower()
            i += 2
        elif args[i] in ("--port", "-p") and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] in ("--host",) and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        else:
            i += 1

    if transport == "stdio":
        asyncio.run(_run_stdio())
    elif transport in ("http", "streamable-http"):
        _run_http(host, port)
    else:
        print(f"Unknown transport: {transport!r}.  Use 'stdio' or 'http'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
