# File Skill

## Description
Read, write, and edit files on the local filesystem. Allows the AI to inspect code, modify configurations, create new files, and manage the project.

## Brief
当用户要求读取文件、修改代码、创建新文件、查看目录结构或搜索文件内容时调用。

## Permissions
- filesystem

## Tools
- `read_file(path: str, offset: int = 0, limit: int = 200) -> str`
- `write_file(path: str, content: str) -> str`
- `edit_file(path: str, old_string: str, new_string: str) -> str`
- `list_dir(path: str = ".") -> str`
