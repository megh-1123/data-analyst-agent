"""MCP server that gives an AI read-only access to sales.db.

Tools: list_tables, describe_table, run_query, create_chart
"""
import sqlite3
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # draw charts into files, never open a window
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from mcp.server.mcpserver import MCPServer

# Paths (same folder as this file)
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "sales.db"
CHARTS_DIR = BASE_DIR / "charts"

# Max rows returned per query, so a huge result doesn't flood the AI
MAX_ROWS = 100

# Create the server and give it a name
server = MCPServer("sales-db")


def connect() -> sqlite3.Connection:
    """Open the database in READ-ONLY mode.

    mode=ro means SQLite itself refuses any change (DELETE, UPDATE, DROP...).
    This is our real safety layer: the AI can look, but never touch.
    """
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def execute_select(sql: str, max_rows: int) -> dict:
    """Shared helper: safely run a SELECT and return columns + rows, or an error."""
    if not sql.strip().lower().startswith(("select", "with")):
        return {"error": "Only SELECT queries are allowed."}
    try:
        with connect() as conn:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
    except sqlite3.Error as e:
        # We RETURN the error instead of crashing,
        # so the AI can read it, fix its SQL, and try again.
        return {"error": f"SQL error: {e}"}
    return {
        "columns": columns,
        "rows": rows[:max_rows],
        "truncated": len(rows) > max_rows,  # tells the AI if results were cut off
    }


# ---------------- Tool 1 ----------------
@server.tool()
def list_tables() -> list[str]:
    """List all tables in the database. Call this first to see what data exists."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [row[0] for row in rows]


# ---------------- Tool 2 ----------------
@server.tool()
def describe_table(table_name: str) -> dict:
    """Get the columns, types and 3 sample rows of a table. Use before writing SQL."""
    with connect() as conn:
        # Check the table exists (also protects against weird input)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if table_name not in tables:
            return {"error": f"Unknown table '{table_name}'. Available: {sorted(tables)}"}

        columns = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        sample = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 3').fetchall()

    return {
        "table": table_name,
        "columns": [{"name": col[1], "type": col[2]} for col in columns],
        "sample_rows": sample,
    }


# ---------------- Tool 3 ----------------
@server.tool()
def run_query(sql: str) -> dict:
    """Run a read-only SQL SELECT query (SQLite dialect) and return the results.

    If the query fails, the error message is returned so you can fix the SQL and retry.
    """
    return execute_select(sql, MAX_ROWS)


# ---------------- Tool 4 ----------------
@server.tool()
def create_chart(sql: str, chart_type: str, title: str) -> dict:
    """Create a chart from a SQL query and save it as a PNG image.

    Use when the user asks to show, plot, chart, visualize, or see a trend.
    The SQL must return exactly 2 columns: labels first, numbers second.
    chart_type: 'bar' to compare categories, 'line' for trends over time
    (order by date), 'pie' for share of a total (max 8 slices).
    Returns the saved chart file path, or an error you can fix and retry.
    """
    if chart_type not in ("bar", "line", "pie"):
        return {"error": "chart_type must be 'bar', 'line' or 'pie'."}

    data = execute_select(sql, max_rows=50)
    if "error" in data:
        return data
    if len(data["columns"]) != 2:
        return {"error": f"Query must return exactly 2 columns (label, number), "
                         f"got {len(data['columns'])}: {data['columns']}"}
    if not data["rows"]:
        return {"error": "Query returned no rows, nothing to plot."}
    if data["truncated"]:
        return {"error": "Too many rows for a readable chart (max 50). Group or filter more."}

    labels = [str(row[0]) for row in data["rows"]]
    try:
        values = [float(row[1]) for row in data["rows"]]
    except (TypeError, ValueError):
        return {"error": "Second column must contain numbers."}

    if chart_type == "pie" and (len(values) > 8 or min(values) < 0):
        return {"error": "Pie charts need 8 or fewer slices and no negative values. Use 'bar'."}

    # ----- Draw the chart -----
    fig, ax = plt.subplots(figsize=(9, 5))
    positions = range(len(labels))  # plot by position, show labels as text
    if chart_type == "bar":
        ax.bar(positions, values, color="#4C72B0")
    elif chart_type == "line":
        ax.plot(positions, values, marker="o", color="#4C72B0")
        ax.grid(alpha=0.3)
    else:
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")

    if chart_type != "pie":
        ax.set_xlabel(data["columns"][0])
        ax.set_ylabel(data["columns"][1])
        ax.set_xticks(list(positions), labels)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        if len(labels) > 6:
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    ax.set_title(title)
    fig.tight_layout()

    # ----- Save it with a unique name -----
    CHARTS_DIR.mkdir(exist_ok=True)
    filename = f"chart_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    path = CHARTS_DIR / filename
    fig.savefig(path, dpi=120)
    plt.close(fig)  # free memory; important in a long-running server

    return {
        "status": "Chart created and already shown to the user. Do not mention "
                  "the file path. Describe the key numbers below in your answer.",
        "chart_path": str(path),
        "data": {"columns": data["columns"], "rows": data["rows"]},
    }


# Start the server when this file is run
if __name__ == "__main__":
    server.run()