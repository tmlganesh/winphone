"""Register WINPHONE with Claude Code (user scope) and install its agent skill. Reversible.

Docs: https://docs.claude.com/en/docs/claude-code/mcp (`claude mcp add|get|remove`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from winphone_core.errors import AdapterError, ToolMissing

SERVER_NAME = "winphone"
HOME = Path.home()


def server_command() -> list[str]:
    """How Claude Code starts the MCP server: the frozen winphone.exe, or this Python environment."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "mcp"]
    return [sys.executable, "-m", "winphone_cli", "mcp"]


def skill_source() -> Path:
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "skills" / "claude-code" / "winphone" / "SKILL.md"
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parents[4] / "skills" / "claude-code" / "winphone" / "SKILL.md"


def _skill_dir() -> Path:
    return HOME / ".claude" / "skills" / SERVER_NAME


def _claude() -> str:
    exe = shutil.which("claude")
    if exe is None:
        add = " ".join(["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", *server_command()])
        raise ToolMissing("Claude Code's `claude` command is not on PATH", hint=f"Install Claude Code, or run: {add}")
    return exe


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)


def install() -> list[str]:
    claude = _claude()
    _run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME])  # replace an older registration
    r = _run([claude, "mcp", "add", "--scope", "user", SERVER_NAME, "--", *server_command()])
    if r.returncode != 0:
        raise AdapterError(f"claude mcp add failed: {(r.stderr or r.stdout).strip()[-300:]}")
    dest = _skill_dir()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_source(), dest / "SKILL.md")
    return [f"Registered the '{SERVER_NAME}' MCP server with Claude Code (user scope).",
            f"Installed the agent skill in {dest}.",
            "Start a new Claude Code session to use it."]


def status() -> list[str]:
    r = _run([_claude(), "mcp", "get", SERVER_NAME])
    registered = r.returncode == 0
    skill = (_skill_dir() / "SKILL.md").is_file()
    return [f"MCP server: {'registered' if registered else 'not registered'}",
            f"Agent skill: {'installed' if skill else 'not installed'} ({_skill_dir()})"]


def uninstall() -> list[str]:
    _run([_claude(), "mcp", "remove", "--scope", "user", SERVER_NAME])
    shutil.rmtree(_skill_dir(), ignore_errors=True)
    return [f"Removed the '{SERVER_NAME}' MCP server and its skill from Claude Code."]
