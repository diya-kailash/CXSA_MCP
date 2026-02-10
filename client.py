"""Gemini-based MCP Client – Cross System Correlation Agent.

This client connects to a remote MCP server via Streamable HTTP, discovers
all available tools, and uses Google Gemini to act as Agent 2 (Cross System
Correlation Agent) in the complaint resolution pipeline.  It gathers
context from the MCP server tools and assembles a complete context package.

Usage:
    # Set your Gemini API key in .env or as env var
    python client.py

    # Or pass a custom complaint
    python client.py "I was charged twice for order 15, please help"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types as genai_types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

GEMINI_MODEL = "gemini-2.5-flash"

MCP_SERVER_URL = "https://r28p3c7r-8001.inc1.devtunnels.ms/mcp/"

# ═══════════════════════════════════════════════════════════════════════════
# System Prompt – Cross System Correlation Agent
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are the **Cross System Correlation Agent** — the data-gathering engine of a complaint resolution pipeline for an Indian e-commerce platform.

You have access to an MCP server with 30 tools spanning customers, orders, complaints, payment logs, logistics logs, and business analytics.

## Your Objective

Given a raw customer complaint, you must:
1. **Understand the complaint** — extract entities (customer ID, order ID, tracking number, etc.) and identify the core issue.
2. **Plan your investigation** — determine what context you need to gather.
3. **Call MCP tools** to retrieve all required data.
4. **Assemble a comprehensive analysis** combining all gathered context.

## Investigation Strategy

### Step 1 — Entity Extraction
Parse the complaint for identifiable information:
- Customer ID, name, email, phone
- Order ID(s), tracking numbers
- Complaint ID (if follow-up)
- Payment method, amounts
- Category hints (delivery, billing, product, service, account)
- Priority/urgency indicators

### Step 2 — Data Gathering (use MCP tools)
Follow this priority order:

**If you have a complaint_id:**
→ Start with `get_complaint_context_logs` — it returns complaint + order + user + payment logs + logistics logs in ONE call.

**If you have an order_id:**
→ Start with `get_order_fulfillment_timeline` — returns order + payment events + logistics events + complaints.

**If you have a customer_id / user_id:**
→ Use `get_user_summary` for profile overview
→ Use `correlate_user_issues` for orders ↔ complaints view
→ Use `get_user_lifetime_value` for lifetime metrics

**If you only have a name/email:**
→ Use `search_users` to resolve the user_id first, then proceed.

**If you only have a tracking number:**
→ Use `get_order_by_tracking` to resolve the order_id first, then proceed.

**Additional context to gather based on complaint type:**
- **Billing issues**: `get_payment_logs` for the order, `get_payment_failure_rate` for systemic issues
- **Delivery issues**: `get_logistics_logs`, `get_order_delivery_time`, `get_carrier_performance`
- **General issues**: `get_complaint_statistics`, `get_dashboard_summary`

### Step 3 — Analysis & Report
After gathering all data, produce a comprehensive markdown report that includes:
1. **Complaint Summary** — what the customer is experiencing
2. **Customer Profile** — who they are, their history
3. **Order Details** — the order(s) involved
4. **Root Cause Analysis** — what went wrong based on the data
5. **Evidence** — specific data points (timestamps, transaction IDs, event sequences)
6. **Impact Assessment** — scope of the issue (isolated vs. systemic)
7. **Recommended Actions** — immediate and long-term fixes

## Important Rules
- **Be thorough** — call multiple tools to build a complete picture
- **Preserve raw data** — include specific IDs, timestamps, amounts in your analysis
- **Correlate across domains** — connect payment events with logistics events with complaints
- **Acknowledge gaps** — if data is missing or insufficient, say so
- **Be specific** — cite transaction IDs, event types, timestamps, not just general observations
"""


# ═══════════════════════════════════════════════════════════════════════════
# MCP ↔ Gemini bridge
# ═══════════════════════════════════════════════════════════════════════════


def mcp_tool_to_gemini_declaration(tool) -> dict[str, Any]:
    """Convert an MCP Tool object to a Gemini function declaration dict."""
    schema = dict(tool.inputSchema) if tool.inputSchema else {}
    # Remove keys that Gemini doesn't accept in FunctionDeclaration schemas
    schema.pop("additionalProperties", None)

    # Recursively clean nested schemas
    def _clean_schema(s: dict) -> dict:
        s.pop("additionalProperties", None)
        if "properties" in s:
            for prop in s["properties"].values():
                if isinstance(prop, dict):
                    _clean_schema(prop)
        return s

    _clean_schema(schema)

    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": schema if schema.get("properties") else None,
    }


async def run_agent(user_complaint: str) -> str:
    """Connect to the MCP server, discover tools, and run the Gemini agent loop."""

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GOOGLE_API_KEY in .env file or as environment variable.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    print(f"🔌 Connecting to MCP server at {MCP_SERVER_URL}...")
    import httpx
    http_client = httpx.AsyncClient(verify=False, follow_redirects=True, timeout=httpx.Timeout(120.0))
    async with streamable_http_client(MCP_SERVER_URL, http_client=http_client) as (read_stream, write_stream, _):
      async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("✅ MCP session initialised")

            # ── Discover tools ────────────────────────────────────────────
            tools_result = await session.list_tools()
            mcp_tools = tools_result.tools
            print(f"🔧 Discovered {len(mcp_tools)} MCP tools")

            # Convert MCP tools → Gemini function declarations
            gemini_declarations = []
            for t in mcp_tools:
                decl = mcp_tool_to_gemini_declaration(t)
                gemini_declarations.append(decl)

            gemini_tool = genai_types.Tool(
                function_declarations=[
                    genai_types.FunctionDeclaration(**d) for d in gemini_declarations
                ]
            )

            # ── Build conversation ────────────────────────────────────────
            messages: list[genai_types.Content] = []

            user_msg = genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=(
                    f"A customer has filed the following complaint. Investigate it thoroughly "
                    f"using the available MCP tools and produce a comprehensive correlation report.\n\n"
                    f"**Customer Complaint:**\n{user_complaint}"
                ))],
            )
            messages.append(user_msg)

            print(f"\n{'='*70}")
            print("📝 CUSTOMER COMPLAINT")
            print(f"{'='*70}")
            print(user_complaint)
            print(f"{'='*70}\n")

            # ── Agent loop: Gemini calls tools until done ─────────────────
            iteration = 0
            max_iterations = 20  # safety cap

            while iteration < max_iterations:
                iteration += 1
                print(f"\n--- Agent iteration {iteration} ---")

                # Rate-limit: pause before each LLM call
                if iteration > 1:
                    print("  ⏳ Waiting 3 seconds before next LLM call...")
                    await asyncio.sleep(3)
                else:
                    await asyncio.sleep(1)

                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=messages,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=[gemini_tool],
                        temperature=0.1,
                    ),
                )

                # Check if model wants to call functions
                candidate = response.candidates[0]
                parts = candidate.content.parts

                has_function_calls = any(
                    p.function_call is not None for p in parts
                )

                if not has_function_calls:
                    # Model is done — extract final text
                    messages.append(candidate.content)
                    final_text = "".join(
                        p.text for p in parts if p.text
                    )
                    print("\n✅ Agent finished — producing final report\n")
                    return final_text

                # Process function calls
                messages.append(candidate.content)

                function_response_parts = []
                for part in parts:
                    if part.function_call is None:
                        continue

                    fn_name = part.function_call.name
                    fn_args = dict(part.function_call.args) if part.function_call.args else {}

                    print(f"  🔧 Calling tool: {fn_name}({json.dumps(fn_args, default=str)})")

                    # Retry logic for dev tunnel timeouts / transient errors
                    result_text = ""
                    for attempt in range(3):
                        try:
                            if attempt > 0:
                                wait = 2 * attempt
                                print(f"    🔄 Retry {attempt}/2 in {wait}s...")
                                await asyncio.sleep(wait)
                            result = await session.call_tool(fn_name, fn_args)
                            result_text = ""
                            for content_item in result.content:
                                if hasattr(content_item, "text"):
                                    result_text += content_item.text

                            # Truncate very large results for Gemini context
                            if len(result_text) > 30000:
                                result_text = result_text[:30000] + "\n... [truncated]"

                            print(f"    ✅ Got {len(result_text)} chars of data")
                            break  # success
                        except Exception as e:
                            if attempt == 2:
                                result_text = json.dumps({"error": str(e)})
                                print(f"    ❌ Error (final): {e}")
                            else:
                                print(f"    ⚠️  Attempt {attempt+1} failed: {e}")

                    function_response_parts.append(
                        genai_types.Part.from_function_response(
                            name=fn_name,
                            response={"result": result_text},
                        )
                    )

                    # Small delay between tool calls to avoid overwhelming dev tunnel
                    await asyncio.sleep(1)

                    function_response_parts.append(
                        genai_types.Part.from_function_response(
                            name=fn_name,
                            response={"result": result_text},
                        )
                    )

                # Send function results back to Gemini
                messages.append(
                    genai_types.Content(
                        role="user",
                        parts=function_response_parts,
                    )
                )

            return "ERROR: Agent exceeded maximum iterations without producing a final report."


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_COMPLAINT = (
    "customer with id 6 reported that order with id 17 has been delivered incorectly. fetch all the details about the customer"
)


def main():
    complaint = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_COMPLAINT

    print(f"\n{'='*70}")
    print("🤖 CXSA – Cross System Correlation Agent (Gemini + MCP)")
    print(f"{'='*70}\n")

    report = asyncio.run(run_agent(complaint))

    print(f"\n{'='*70}")
    print("📊 CORRELATION AGENT REPORT")
    print(f"{'='*70}\n")
    print(report)


if __name__ == "__main__":
    main()
