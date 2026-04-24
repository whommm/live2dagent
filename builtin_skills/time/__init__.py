"""Time skill: provide current time and date information."""

from __future__ import annotations

from datetime import datetime, timedelta


def get_current_time() -> str:
    """Get the current local time in a human-readable format."""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


def get_current_date() -> str:
    """Get the current date in a human-readable format."""
    now = datetime.now()
    return now.strftime("%Y年%m月%d日 %A")


def get_time_in_city(city: str) -> str:
    """Get the current time for a specific city (approximate, using UTC offsets).

    Supported cities: Beijing, Tokyo, London, New York, Paris, Sydney, Moscow.
    """
    from datetime import timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    offsets = {
        "beijing": 8,
        "tokyo": 9,
        "london": 0,
        "new york": -5,
        "paris": 1,
        "sydney": 11,
        "moscow": 3,
    }
    key = city.lower().strip()
    offset = offsets.get(key)
    if offset is None:
        return f"Unknown city '{city}'. Supported: {', '.join(offsets.keys())}"
    local = now + timedelta(hours=offset)
    return f"{city} 当前时间：{local.strftime('%Y-%m-%d %H:%M:%S')} (UTC{'+' if offset >= 0 else ''}{offset})"


tools = {
    "get_current_time": get_current_time,
    "get_current_date": get_current_date,
    "get_time_in_city": get_time_in_city,
}
