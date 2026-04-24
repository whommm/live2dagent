# Weather Skill

## Description
查询指定城市的实时天气信息。

## Brief
当用户询问某个城市的天气、气温、是否下雨或穿衣建议时调用。

## Permissions
- network

## Tools
- `get_weather(city: str) -> dict`

## Examples
用户: "北京今天天气怎么样？"
→ 调用 `get_weather("北京")`
