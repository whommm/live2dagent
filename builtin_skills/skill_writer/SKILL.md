# Skill Writer

## Description
Create, update, install, and delete live2dagent skills programmatically. A skill is a self-contained plugin consisting of a `SKILL.md` descriptor and a Python `__init__.py` module that exposes a `tools` dictionary.

## Brief
当用户要求"帮我写一个能...的 skill"、"添加一个新功能"、"写一个工具"时调用。

## Permissions
- filesystem

## Tools
- `create_skill(skill_id: str, description: str, skill_md: str, tools_code: str) -> str`
- `update_skill(skill_id: str, skill_md: str, tools_code: str) -> str`
- `install_skill(skill_id: str) -> str`
- `delete_skill(skill_id: str) -> str`

## Skill Authoring Workflow

你必须严格遵守以下流程：

1. **create_skill** — 把代码写入草稿目录。
2. **test_skill** — 调用 skill_tester:test_skill 进行完整测试。
3. **修复** — 如果测试未通过，分析错误原因，调用 update_skill 修改代码，然后再次测试。
4. **重复 2-3** — 直到 test_skill 返回 `"passed": true`。
5. **install_skill** — 只有测试通过后，才调用 install_skill 将草稿移动到正式目录并生效。

## Code Constraints

- 只能使用 **标准库 + httpx + pydantic**。
- 禁止导入: `os`, `subprocess`, `sys`, `socket`, `ctypes`, `threading`, `multiprocessing`, `pickle`, `marshal`。
- 禁止调用: `eval`, `exec`, `compile`, `__import__`, `breakpoint`。
- 所有工具函数必须有 **类型注解** 和 **docstring**。
- `__init__.py` 必须暴露 `tools = {"tool_name": function, ...}` 字典。
- 函数可以是 sync 或 async。

## Example tools_code

```python
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
```
