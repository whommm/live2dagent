# Shell Skill

## Description
Execute system shell commands and return output. Gives the AI direct access to the operating system to run programs, inspect system state, and perform automated tasks.

## Brief
当用户要求运行命令、查看系统信息、操作程序或执行任何需要命令行的任务时调用。

## Permissions
- filesystem
- network
- process

## Tools
- `run_command(command: str, timeout: int = 30) -> str`
