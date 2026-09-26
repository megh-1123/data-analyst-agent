"""Test the MCP server: connect like a real client and call each tool."""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # How to start the server: use the same Python (from venv) to run server.py
    params = StdioServerParameters(command=sys.executable, args=["server.py"])

    # Start the server and open a connection to it
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()  # handshake: client and server say hello

            # 1. Ask the server which tools it offers
            tools = await session.list_tools()
            print("Tools available:", [tool.name for tool in tools.tools])

            # 2. Call each tool with some test inputs
            tests = [
                ("list_tables", {}),
                ("describe_table", {"table_name": "products"}),
                ("run_query", {"sql": "SELECT city, COUNT(*) AS customers FROM customers GROUP BY city ORDER BY customers DESC"}),
                ("describe_table", {"table_name": "employees"}),   # table doesn't exist -> error
                ("run_query", {"sql": "SELECT revenue FROM orders"}),   # bad column -> error
                ("run_query", {"sql": "DELETE FROM orders"}),           # not allowed -> blocked
            ]

            for tool_name, arguments in tests:
                result = await session.call_tool(tool_name, arguments)
                text = " | ".join(block.text for block in result.content)
                print(f"\n>> {tool_name}({arguments})")
                print(text[:300])  # show first 300 characters


# Run the async main function
asyncio.run(main())