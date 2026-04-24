"""Shell skill: execute system commands."""

from __future__ import annotations

import asyncio
import shlex

# Block shell metacharacters to prevent command injection
_FORBIDDEN_SHELL_CHARS = frozenset(";|&`$(){}<>\\\n\r")


async def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command and return stdout + stderr.

    Security: uses create_subprocess_exec with shlex.split instead of
    create_subprocess_shell, and rejects strings containing shell
    metacharacters.
    """
    if any(ch in command for ch in _FORBIDDEN_SHELL_CHARS):
        return (
            "Error: Shell metacharacters are not allowed for security reasons. "
            "Use simple commands without pipes, redirects, or substitutions."
        )

    try:
        args = shlex.split(command)
    except ValueError as exc:
        return f"Error: Invalid command syntax: {exc}"

    if not args:
        return "Error: Empty command"

    proc = await asyncio.create_subprocess_exec(
        args[0],
        *args[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Error: Command timed out after {timeout}s"

    result = ""
    if stdout:
        result += stdout.decode("utf-8", errors="replace")
    if stderr:
        result += "\n[stderr]\n" + stderr.decode("utf-8", errors="replace")
    return result or "(no output)"


tools = {
    "run_command": run_command,
}
