# MCP Context Service – Indian E-Commerce

A production-quality **MCP server** backed by SQLite, featuring a rich Indian
e-commerce dataset.  Designed for agent-driven reasoning, root-cause analysis,
business intelligence and customer-support automation.

## Data Model

| Table | Rows | Key columns |
|-------|------|-------------|
| **users** | 20 | name, email, phone, address, city, state, zip_code, country (India) |
| **orders** | 50 | user_id, item, quantity, unit_price, total_amount (₹), status, payment_method, tracking |
| **complaints** | 25 | user_id, order_id, category, priority, status, subject, details, resolution, assigned_to |
| **payment_logs** | 54 | order_id, transaction_id, event_type, amount, currency (INR), gateway |
| **logistics_logs** | 65 | order_id, tracking_number, carrier, event_type, location |

**214 seed rows** are inserted automatically on first run.  All data is
Indian-centric – ₹ INR currency, Indian cities/states/pin codes, Indian
payment gateways (Razorpay, Paytm, PhonePe, Cashfree, BillDesk, CCAvenue),
Indian carriers (BlueDart, Delhivery, DTDC, Ekart, XpressBees, India Post,
Shadowfax, Ecom Express), and Indian payment methods (UPI, net banking,
wallet, COD, EMI, credit/debit card).

## Architecture

```
app/
├── config.py       # Settings (db path, defaults)
├── db.py           # Schema DDL, connection helpers, seeding from JSON
├── repository.py   # 25+ read-only query functions
├── mcp_server.py   # MCP tools/resources/prompts, CLI entry
├── __main__.py     # Entry point (python -m app)
└── __init__.py
data/
└── seed.json       # All seed data (20 users, 50 orders, etc.)
```

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\activate      # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

## Run MCP Server

### stdio (default – for Claude Desktop / local clients)

```bash
python -m app.mcp_server
```

### HTTP (Streamable HTTP – for remote / web clients)

```bash
python -m app.mcp_server --transport http              # default port 8001
python -m app.mcp_server --transport http --port 9000   # custom port
python -m app.mcp_server --transport http --host 0.0.0.0 --port 8001
```

The MCP endpoint will be available at `http://<host>:<port>/mcp`.

### Claude Desktop configuration

**stdio transport:**
```json
{
  "mcpServers": {
    "SampleMCPServer": {
      "command": "C:/path/to/.venv/Scripts/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:/path/to/DiyaSampleMCP"
    }
  }
}
```

**HTTP transport** (start the server first, then configure):
```json
{
  "mcpServers": {
    "SampleMCPServer": {
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

## MCP Capabilities

### 30 Tools

| Category | Tools |
|----------|-------|
| **Users** | `list_users`, `get_user_by_id`, `search_users`, `get_user_summary`, `get_user_lifetime_value` |
| **Orders** | `list_orders`, `get_order_by_id`, `get_orders_by_date_range`, `get_order_by_tracking`, `get_order_statistics`, `get_order_fulfillment_timeline`, `get_active_shipments`, `get_order_delivery_time` |
| **Complaints** | `list_complaints`, `get_complaint_by_id`, `search_complaints`, `get_high_priority_open_complaints`, `get_complaint_statistics`, `get_complaints_for_order`, `get_complaint_resolution_time_stats` |
| **RCA** | `correlate_user_issues`, `get_complaint_context_logs` |
| **Logs** | `get_payment_logs`, `get_logistics_logs` |
| **Analytics** | `get_revenue_by_city`, `get_top_customers`, `get_dashboard_summary`, `get_payment_failure_rate`, `get_payment_summary_by_method`, `get_carrier_performance` |

### 13 Resources

| URI | Description |
|-----|-------------|
| `context://data/users` | Full users table |
| `context://data/orders` | Full orders table |
| `context://data/complaints` | Full complaints table |
| `context://stats/orders` | Order stats by status & payment method |
| `context://stats/complaints` | Complaint stats by category, priority, status & agent |
| `context://stats/revenue-by-city` | Revenue per city |
| `context://stats/top-customers` | Top 10 customers by spend |
| `context://stats/payment-failure` | Payment gateway failure rates |
| `context://stats/carrier-performance` | Carrier delivery metrics |
| `context://alerts/high-priority` | Urgent complaint queue |
| `context://logs/payments` | All payment logs |
| `context://logs/logistics` | All logistics logs |
| `context://dashboard/summary` | High-level dashboard |

### 9 Prompts

| Prompt | Description |
|--------|-------------|
| `user_360_view` | Comprehensive customer profile with order history and risk signals |
| `root_cause_analysis` | RCA for a complaint with context and similar complaints |
| `deep_root_cause_analysis` | Cross-domain RCA using payment/logistics logs |
| `escalation_review` | Review all high-priority open complaints |
| `order_investigation` | End-to-end order analysis with payment & logistics timeline |
| `system_health_overview` | Business dashboard across all domains |
| `customer_churn_risk` | Churn risk assessment with retention recommendations |
| `regional_performance_review` | Revenue & performance analysis across Indian cities |
| `payment_health_audit` | Payment gateway reliability & method analysis |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_DB_PATH` | `data/app.db` | Path to SQLite database file |
