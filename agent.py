"""Data Analyst Agent: answers questions about sales.db using Gemini + MCP tools."""
import asyncio
import json
import logging
import os
import random
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_STEPS = 10             # safety limit: stop if the agent loops too long
MAX_RETRIES = 4            # how many times to try one Gemini call
RETRYABLE_CODES = {429, 500, 503, 504}  # temporary errors worth retrying
OPEN_CHARTS = True         # open new charts automatically (Windows)

# Log everything to agent.log (for debugging), keep the terminal clean
logging.basicConfig(
    filename="agent.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("agent")

SYSTEM_PROMPT = """You are a data analyst agent for an online shop. You answer questions
by querying a SQLite database using the tools available to you.

How to work:
1. The database schema is given below. Use only those tables and columns.
   Never guess column names. (list_tables / describe_table are available
   if you ever need to re-check.)
2. Write SQLite SQL and run it with run_query.
3. If a query returns an error, read the error, fix the SQL and try again.
4. Answer in plain English, with the key numbers. Keep it short.

Charts:
- When the user asks to show, plot, chart, visualize or see a trend, call
  create_chart. Use 'bar' to compare categories, 'line' for time trends
  (order by date), 'pie' only for share of a total with few slices.
- Give the two SQL columns readable names with AS, e.g. AS month, AS revenue,
  because they become the axis labels.
- For chart requests, call create_chart directly. Don't run the same query
  with run_query first: create_chart already returns the data it plotted.
- After making a chart, still state the key numbers or insight in words.
  Don't mention the file path; the app shows the chart.

Business rules:
- Revenue = products.price * order_items.quantity.
- Only count orders with status 'delivered' or 'shipped' as sales,
  unless the user asks otherwise. Say which statuses you included.
- Prices are in Indian Rupees (₹).
- If a question can't be answered from the data, say so honestly."""


async def load_schema(session) -> str:
    """Read every table's structure ONCE at startup, using our own MCP tools."""
    result = await session.call_tool("list_tables", {})
    tables = [b.text for b in result.content if hasattr(b, "text")]
    parts = []
    for table in tables:
        result = await session.call_tool("describe_table", {"table_name": table})
        parts.append("\n".join(b.text for b in result.content if hasattr(b, "text")))
    return "\n\n".join(parts)


def mcp_tools_to_gemini(mcp_tools) -> types.Tool:
    """Convert the MCP server's tool list into Gemini's tool format."""
    declarations = [
        types.FunctionDeclaration(
            name=tool.name,
            description=tool.description,
            parameters_json_schema=tool.input_schema,  # MCP already gives a JSON schema
        )
        for tool in mcp_tools
    ]
    return types.Tool(function_declarations=declarations)


def suggested_wait(e: errors.APIError):
    """Read the wait time Google suggests in a 429 error (e.g. 'retryDelay': '37s')."""
    try:
        for detail in e.details["error"].get("details", []):
            if "retryDelay" in detail:
                return float(detail["retryDelay"].rstrip("s"))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None


def is_daily_limit(e: errors.APIError) -> bool:
    """True if a 429 is the DAILY quota (retrying won't help until it resets)."""
    return e.code == 429 and "PerDay" in str(e.details)


async def ask_gemini(client, history, config):
    """Call Gemini, retrying temporary errors (busy / rate limit) with growing waits."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await client.aio.models.generate_content(
                model=MODEL, contents=history, config=config
            )
        except errors.APIError as e:
            # Permanent error (bad key, bad request) or out of attempts: give up
            if e.code not in RETRYABLE_CODES or attempt == MAX_RETRIES:
                raise
            if is_daily_limit(e):
                raise  # daily quota used up: waiting a minute won't fix it
            if e.code == 429:
                # Rate limit: the quota resets per minute, so short waits don't help.
                # Use Google's suggested wait if it gives one, otherwise 20s.
                wait = min(suggested_wait(e) or 20, 60) + random.uniform(0, 1)
            else:
                # Server busy: exponential backoff, wait 2s, 4s, 8s (+ randomness)
                wait = 2 ** attempt + random.uniform(0, 1)
            print(f"   (Gemini busy [{e.code}], retrying in {wait:.0f}s... "
                  f"attempt {attempt + 1}/{MAX_RETRIES})")
            log.warning("Gemini error %s, retry %d in %.1fs", e.code, attempt, wait)
            await asyncio.sleep(wait)


def chart_path_from(tool_output: str):
    """If a create_chart call succeeded, return the saved image path."""
    try:
        return json.loads(tool_output).get("chart_path")
    except (json.JSONDecodeError, AttributeError):
        return None


def hide_chart_path(tool_output: str) -> str:
    """Remove the file path before Gemini sees the result, so it can't mention it."""
    data = json.loads(tool_output)
    data.pop("chart_path", None)
    return json.dumps(data)


async def run_agent(question, session, client, config, history):
    """The agent loop: think -> call tools -> read results -> repeat -> answer.

    Returns (answer, steps, charts), where charts is a list of image paths.
    """
    start = len(history)  # remember where this question starts in memory
    charts = []           # chart images created while answering
    history.append(types.Content(role="user", parts=[types.Part(text=question)]))
    log.info("QUESTION: %s", question)

    try:
        for step in range(1, MAX_STEPS + 1):
            # 1. Ask Gemini what to do next (with automatic retries)
            response = await ask_gemini(client, history, config)
            if not response.candidates or not response.candidates[0].content:
                raise RuntimeError("Gemini returned an empty response")
            history.append(response.candidates[0].content)

            # 2. Did Gemini ask for any tools?
            calls = response.function_calls or []
            if not calls:
                answer = response.text or "(The model gave no answer.)"
                log.info("ANSWER (%d steps): %s", step, answer)
                return answer, step, charts

            # 3. Run each requested tool on the MCP server
            results = []
            for call in calls:
                args = dict(call.args or {})
                print(f"   [step {step}] {call.name}({args})")
                result = await session.call_tool(call.name, args)
                output = "\n".join(b.text for b in result.content if hasattr(b, "text"))
                log.info("TOOL %s %s -> %s", call.name, args, output[:500])
                if call.name == "create_chart":
                    path = chart_path_from(output)
                    if path:
                        charts.append(path)                # we keep the path for the app
                        output = hide_chart_path(output)   # Gemini never sees it
                results.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={"result": output},
                )))

            # 4. Send the tool results back to Gemini, then loop again
            history.append(types.Content(role="user", parts=results))

    except Exception:
        # Failed half-way: remove this question's partial steps from memory,
        # so the next question starts from a clean conversation.
        del history[start:]
        raise

    del history[start:]
    log.warning("STEP LIMIT reached for: %s", question)
    return "Sorry, I couldn't finish within the step limit. Try a simpler question.", MAX_STEPS, []


def friendly_error(e: Exception) -> str:
    """Turn technical errors into messages a user understands."""
    if isinstance(e, errors.APIError):
        if is_daily_limit(e):
            return ("Daily free quota used up for this model. Try again after it resets, "
                    "or switch GEMINI_MODEL in .env to another model.")
        if e.code == 429:
            return "Rate limit reached. Please wait a minute and ask again."
        if e.code in (500, 503, 504):
            return "Gemini is overloaded right now. Please try again in a little while."
        if e.code in (400, 401, 403):
            return f"Request rejected ({e.code}). Check your API key and model name in .env."
        if e.code == 404:
            return "Model not found. Check GEMINI_MODEL in your .env file."
    return f"Something went wrong: {e}"


async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not found. Check your .env file.")
    client = genai.Client(api_key=api_key)

    server = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools

            # Give Gemini the database structure up front, so it doesn't spend
            # 3-4 API calls per question exploring tables. Saves quota and time.
            schema = await load_schema(session)
            full_prompt = SYSTEM_PROMPT + "\n\nDatabase schema (with sample rows):\n" + schema

            config = types.GenerateContentConfig(
                system_instruction=full_prompt,
                tools=[mcp_tools_to_gemini(tools)],
                # We run the tool loop ourselves, so turn off the SDK's automatic mode
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            print(f"Data Analyst Agent ready ({MODEL}). Tools: {[t.name for t in tools]}")
            print("Ask a question about the sales data. Type 'exit' to quit.\n")

            history = []  # conversation memory, so follow-up questions work
            while True:
                question = input("You: ").strip()
                if question.lower() in ("exit", "quit"):
                    break
                if not question:
                    continue

                started = time.perf_counter()
                try:
                    answer, steps, charts = await run_agent(
                        question, session, client, config, history)
                    seconds = time.perf_counter() - started
                    print(f"\nAgent: {answer}")
                    for path in charts:
                        print(f"   Chart saved: {path}")
                        if OPEN_CHARTS and sys.platform == "win32":
                            os.startfile(path)  # opens in your default image viewer
                    print(f"   ({steps} steps, {seconds:.1f}s)\n")
                except Exception as e:
                    log.exception("FAILED: %s", question)
                    print(f"\nAgent: {friendly_error(e)}\n")


if __name__ == "__main__":
    asyncio.run(main())