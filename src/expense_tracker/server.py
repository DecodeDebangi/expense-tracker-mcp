"""MCP entry point. Tools are added here in Phase 1."""

from mcp.server import MCPServer

server = MCPServer("expense-tracker")


def main() -> None:
    server.run()
