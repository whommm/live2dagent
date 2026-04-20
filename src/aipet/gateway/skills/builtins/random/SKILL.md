# Random Skill

## Description
Generate random numbers, make random choices, flip coins, and roll dice.

## Brief
当用户需要随机数、随机做选择、抛硬币、掷骰子或抽奖时调用。

## Permissions

## Tools
- `random_number(min: int = 1, max: int = 100) -> str`
  - Generate a random integer in the given range.
- `random_choice(options: str) -> str`
  - Pick a random item from a comma-separated list.
- `flip_coin() -> str`
  - Returns "heads" or "tails".
- `roll_dice(sides: int = 6) -> str`
  - Roll a dice with the specified number of sides.

## Examples
User: "帮我随机选一个：火锅、烧烤、日料"
→ 调用 `random_choice("火锅, 烧烤, 日料")`

User: "掷个骰子"
→ 调用 `roll_dice(6)`

User: "1到10之间随机一个数"
→ 调用 `random_number(1, 10)`
