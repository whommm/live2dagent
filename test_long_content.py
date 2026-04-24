import json
import httpx

api_key = "sk-b28b3a55114c4a0c868d76a842dd5c42"

long_content = '{"tool_calls": [{"name": "system:get_tool_schema", "id": "schema_abc123", "arguments": {"tool_name": "weather:get_weather"}}]}'

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": long_content, "tool_calls": [{"id": "schema_abc123", "type": "function", "function": {"name": "system:get_tool_schema", "arguments": '{"tool_name": "weather:get_weather"}'}}]},
    {"role": "tool", "content": '{"parameters": [{"name": "city", "type": "string"}]}', "tool_call_id": "schema_abc123"},
]

resp = httpx.post(
    "https://api.deepseek.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={"model": "deepseek-v4-flash", "messages": messages, "temperature": 0.7},
    timeout=30,
)
print(f"Status: {resp.status_code}")
print(f"Reply: {resp.text[:500]}")
