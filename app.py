"""Streamlit web app for the Data Analyst Agent.

Run with:  streamlit run app.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import streamlit as st
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Reuse everything we already built in agent.py
from agent import (MODEL, SYSTEM_PROMPT, friendly_error, is_daily_limit,
                   load_schema, mcp_tools_to_gemini, run_agent)

SERVER_PATH = Path(__file__).parent / "server.py"
EXAMPLES = [
    "Which product category earned the most revenue?",
    "Show the monthly revenue trend",
    "What percentage of orders were cancelled?",
    "Who are the top 5 customers by spending?",
    "Visualize customers by city",
]

# Public demo protection: max questions per visit (0 = unlimited, for local use).
# Set DEMO_QUESTION_LIMIT in the deployment's secrets, not in your local .env.
QUESTION_LIMIT = int(os.getenv("DEMO_QUESTION_LIMIT", "0"))

st.set_page_config(page_title="Data Analyst Agent", page_icon="📊")


# ---------- Setup (runs once per browser session) ----------
if "messages" not in st.session_state:
    st.session_state.messages = []  # what we show on screen
    st.session_state.history = []   # what Gemini remembers (agent memory)
    st.session_state.asked = 0      # questions asked this visit (for the demo limit)

api_key = os.getenv("GEMINI_API_KEY")  # agent.py already loaded .env
if not api_key:
    st.error("GEMINI_API_KEY not found. Add it to your .env file and restart.")
    st.stop()


def tool_calls_since(history, start):
    """List the tool calls Gemini made while answering (to show in the UI)."""
    calls = []
    for content in history[start:]:
        if content.role != "model":
            continue
        for part in content.parts or []:
            if part.function_call:
                calls.append((part.function_call.name, dict(part.function_call.args or {})))
    return calls


async def ask_agent(question, history):
    """Connect to the MCP server, run the agent loop once, then disconnect."""
    server = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            schema = await load_schema(session)
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT
                + "\n\nDatabase schema (with sample rows):\n" + schema,
                tools=[mcp_tools_to_gemini(tools)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            # A fresh Gemini client for each question. Each question runs in its own
            # event loop (asyncio.run), and an async client can't be reused
            # after the loop it was created in has closed.
            client = genai.Client(api_key=api_key)
            return await run_agent(question, session, client, config, history)


def unwrap(error):
    """Errors raised inside the MCP connection come wrapped in an ExceptionGroup.
    Unwrap them so friendly_error() sees the real error (e.g. Gemini 503)."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


def error_message(error):
    """Friendly error text. On the public demo, explain quota limits for visitors."""
    if QUESTION_LIMIT and is_daily_limit(error):
        return ("This free demo has used up today's AI quota. Please try again tomorrow, "
                "or see the README on GitHub for screenshots and how to run it yourself.")
    return friendly_error(error)


def show_message(msg):
    """Draw one chat message: text, charts, and (for the agent) how it got there."""
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        for chart in msg.get("charts", []):
            if Path(chart).exists():
                st.image(chart)
        if msg.get("tool_calls"):
            with st.expander(f"How I got this ({msg['steps']} steps, {msg['seconds']:.1f}s)"):
                for name, args in msg["tool_calls"]:
                    st.markdown(f"**{name}**")
                    if "sql" in args:
                        st.code(args["sql"], language="sql", wrap_lines=True)
                    else:
                        st.json(args)


# ---------- Sidebar ----------
with st.sidebar:
    st.header("📊 Data Analyst Agent")
    st.caption(f"Model: `{MODEL}`  \nData: online shop sales (SQLite)")
    st.subheader("Try asking")
    for example in EXAMPLES:
        if st.button(example, width="stretch"):
            st.session_state.pending = example
    st.divider()
    if st.button("🗑️ Clear chat", width="stretch"):
        st.session_state.messages = []
        st.session_state.history = []
        st.rerun()
    if QUESTION_LIMIT:
        st.info(f"Public demo on a free API tier: {QUESTION_LIMIT} questions per visit. "
                f"You've used {st.session_state.asked}.")
    st.caption("Free-tier API: if you see a rate-limit message, wait a minute.")


# ---------- Main chat ----------
st.title("Ask your sales data")
st.caption("Plain-English questions → SQL → answers and charts. Read-only access.")

for msg in st.session_state.messages:
    show_message(msg)

limit_reached = QUESTION_LIMIT and st.session_state.asked >= QUESTION_LIMIT
if limit_reached:
    st.warning("You've reached the demo limit for this visit. Thanks for trying it! "
               "The full code is on GitHub if you'd like to run it yourself.")

question = st.chat_input("e.g. Which city has the most customers?", disabled=bool(limit_reached))
question = question or st.session_state.pop("pending", None)
if limit_reached:
    question = None  # also ignore sidebar example buttons

if question:
    st.session_state.asked += 1
    user_msg = {"role": "user", "content": question}
    st.session_state.messages.append(user_msg)
    show_message(user_msg)

    history = st.session_state.history
    start = len(history)
    with st.spinner("Analysing your data..."):
        started = time.perf_counter()
        try:
            answer, steps, charts = asyncio.run(ask_agent(question, history))
            agent_msg = {
                "role": "assistant", "content": answer, "charts": charts,
                "steps": steps, "seconds": time.perf_counter() - started,
                "tool_calls": tool_calls_since(history, start),
            }
        except Exception as e:
            agent_msg = {"role": "assistant", "content": f"⚠️ {error_message(unwrap(e))}"}

    st.session_state.messages.append(agent_msg)
    st.rerun()  # redraw the page so the sidebar counter and limit are up to date