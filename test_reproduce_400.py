import json
import httpx

api_key = "sk-b28b3a55114c4a0c868d76a842dd5c42"

# Simulate the exact message sequence after _resolve_tool_calls loop_idx=0
schema_result = json.dumps({
    "name": "weather:get_weather",
    "description": "Get weather for a city",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
    }
}, ensure_ascii=False)

messages = [
    {"role": "system", "content": "You are a helpful assistant. You have tools."},
    {"role": "user", "content": "Explain how you work"},
    {
        "role": "assistant",
        "content": '{"tool_calls": [{"name": "system:get_tool_schema", "id": "system:get_tool_schema_abc123", "arguments": {"tool_name": "weather:get_weather"}}]}',
        "tool_calls": [{
            "id": "system:get_tool_schema_abc123",
            "type": "function",
            "function": {"name": "system:get_tool_schema", "arguments": '{"tool_name": "weather:get_weather"}'}
        }]
    },
    {"role": "tool", "content": schema_result, "tool_call_id": "system:get_tool_schema_abc123"},
]

resp = httpx.post(
    "https://api.deepseek.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={"model": "deepseek-v4-flash", "messages": messages, "temperature": 0.7},
    timeout=30,
)
print(f"Status: {resp.status_code}")
print(f"Body: {resp.text}")
