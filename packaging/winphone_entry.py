"""Entry point frozen into winphone.exe (CLI, daemon and MCP server in one program)."""

from winphone_cli.main import app

if __name__ == "__main__":
    app()
