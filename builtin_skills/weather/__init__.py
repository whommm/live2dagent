"""Weather skill."""

from __future__ import annotations

import httpx


async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    url = f"https://wttr.in/{city}?format=%C+%t"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return f"{city} 当前天气：{resp.text.strip()}"


tools = {
    "get_weather": get_weather,
}
