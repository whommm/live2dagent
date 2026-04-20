# Memory Skill

## Description
Read and update the AI's long-term memory file (memory.md). Allows the AI to remember facts about the user, preferences, and shared experiences across sessions.

## Brief
当对话中出现值得记住的信息（用户喜好、重要事件、约定）时调用，将信息写入长期记忆。

## Permissions
- filesystem

## Tools
- `read_memory() -> str`
- `update_memory(section: str, content: str) -> str`
