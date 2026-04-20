# Time Skill

## Description
提供当前时间、日期和世界各地城市的时间查询。

## Brief
当用户询问现在几点、今天几号、星期几或某个城市的当前时间时调用。

## Permissions

## Tools
- `get_current_time() -> str`
  - 返回当前本地时间，格式：YYYY-MM-DD HH:MM:SS
- `get_current_date() -> str`
  - 返回当前日期，格式：YYYY年MM月DD日 星期X
- `get_time_in_city(city: str) -> str`
  - 查询指定城市的当前时间
  - 支持城市：Beijing, Tokyo, London, New York, Paris, Sydney, Moscow

## Examples
用户: "现在几点了？"
→ 调用 `get_current_time()`

用户: "纽约现在几点？"
→ 调用 `get_time_in_city("New York")`
