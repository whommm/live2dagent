# Skill Tester

## Description
Validate skill drafts before they are installed. Performs static analysis (AST lint), import verification, structure checks, and smoke tests on every tool.

## Brief
在调用 skill_writer:install_skill 之前，必须先调用 skill_tester:test_skill 确认技能通过所有测试。

## Permissions
- filesystem

## Tools
- `test_skill(skill_id: str) -> str`
- `lint_skill(skill_id: str) -> str`

## Test Stages

1. **Lint** — AST 语法检查 + 安全扫描（禁止危险 import / eval / exec 等）。
2. **Import** — 在隔离临时目录中尝试 import 模块。
3. **Structure** — 检查是否存在有效的 `tools = {...}` 字典。
4. **Smoke** — 为每个 tool 根据类型注解生成 mock 参数并调用一次。

返回 JSON 格式：
```json
{
  "passed": true|false,
  "stage": "lint|import|structure|smoke|complete",
  "errors": ["error message", ...],
  "tools": ["tool_name", ...]
}
```
