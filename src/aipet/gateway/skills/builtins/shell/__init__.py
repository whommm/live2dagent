"""Shell skill: execute system commands."""

from __future__ import annotations

import asyncio


async def run_command(command: str, timeout: int = 30) -> str:
    """Run a shell command and return stdout + stderr."""
    proc = await asyncio.create_subprocess_shell(
        command,
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
