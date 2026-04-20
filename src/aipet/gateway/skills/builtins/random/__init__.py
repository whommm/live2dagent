"""Random skill: generate random numbers, choices, and decisions."""

from __future__ import annotations

import random


def random_number(min: int = 1, max: int = 100) -> str:
    """Generate a random integer between min and max (inclusive)."""
    return str(random.randint(min, max))


def random_choice(options: str) -> str:
    """Pick a random option from a comma-separated list.

    Example: options="apple, banana, cherry" -> returns one of them.
    """
    items = [opt.strip() for opt in options.split(",") if opt.strip()]
    if not items:
        return "No valid options provided."
    return random.choice(items)


def flip_coin() -> str:
    """Flip a coin and return 'heads' or 'tails'."""
    return random.choice(["heads", "tails"])


def roll_dice(sides: int = 6) -> str:
    """Roll a dice with the given number of sides (default 6)."""
    return str(random.randint(1, max(1, sides)))


tools = {
    "random_number": random_number,
    "random_choice": random_choice,
    "flip_coin": flip_coin,
    "roll_dice": roll_dice,
}
