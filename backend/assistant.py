"""LifeTracker AI assistant — Claude with read-only tools over the user's own finance data.

Claude never sees raw database access or Plaid tokens. It can only call the small,
read-only functions passed in from main.py, which are already scoped to the signed-in user.
"""
import json
import os
from datetime import date

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOOL_ROUNDS = 8

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


TOOLS = [
    {
        "name": "get_spending_summary",
        "description": "Totals for a date range: total spent, money in, spending by category, by month, and by source "
                       "(Chase, Venmo, etc). Transfers between the user's own accounts are excluded from spending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                "end": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "search_transactions",
        "description": "Find individual transactions. Amounts are positive for money spent and negative for money received. "
                       "All filters are optional; text matches merchant, note, or category (case-insensitive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "YYYY-MM-DD"},
                "end": {"type": "string", "description": "YYYY-MM-DD"},
                "text": {"type": "string"},
                "category": {"type": "string"},
                "source": {"type": "string", "description": "e.g. Chase, Venmo"},
                "min_amount": {"type": "number"},
                "max_amount": {"type": "number"},
                "limit": {"type": "integer", "description": "max rows, default 50, max 200"},
            },
        },
    },
    {
        "name": "get_investments",
        "description": "Latest investment holdings (ticker, name, quantity, value, cost basis) plus portfolio value history.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def system_prompt(context):
    return f"""You are the built-in financial assistant for LifeTracker, a personal finance dashboard.
Today is {date.today().isoformat()}. You are talking with the account owner about their own money.

Data you can see: {context}

How to work:
- Always use the tools to look up real numbers before stating any figure. Never guess or invent amounts.
- Convert relative dates ("last month", "this year") into exact YYYY-MM-DD ranges yourself.
- Be concise and concrete: lead with the answer, then a short breakdown. Use small markdown tables when comparing several items.
- Format money like $1,234.56.
- If the data needed isn't there (e.g. an account isn't connected yet), say so plainly.
- You may give general budgeting observations, but you are not a licensed financial advisor: don't give
  personalized investment recommendations (what to buy or sell). Point out facts and trade-offs instead.
- You cannot change data or move money. If asked, explain what the user can do in the app instead."""


def run_chat(messages, tools_impl, context):
    """messages: [{role: 'user'|'assistant', content: str}, ...]  (text only, from the browser)
    tools_impl: {tool_name: callable(**input) -> JSON-serializable}
    Returns (reply_text, tools_used)."""
    convo = [{"role": m["role"], "content": str(m["content"])[:8000]}
             for m in messages[-20:] if m.get("role") in ("user", "assistant") and m.get("content")]
    if not convo or convo[-1]["role"] != "user":
        raise ValueError("last message must be from the user")

    tools_used = []
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client().messages.create(
            model=MODEL, max_tokens=2000, system=system_prompt(context),
            tools=TOOLS, messages=convo,
        )
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return text or "I couldn't come up with an answer to that.", tools_used

        convo.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            tools_used.append(block.name)
            try:
                fn = tools_impl[block.name]
                out = json.dumps(fn(**(block.input or {})), default=str)[:60000]
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
            except Exception as e:  # report tool errors back to Claude instead of crashing
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Error: {e}", "is_error": True})
        convo.append({"role": "user", "content": results})

    return "That question needed more lookups than I'm allowed in one turn — try narrowing it down.", tools_used
