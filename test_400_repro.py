import json
import httpx

api_key = "sk-b28b3a55114c4a0c868d76a842dd5c42"

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Explain how tool calling works"},
    {
        "role": "assistant",
        "content": '{"tool_calls": [{"name": "file:read_file", "id": "file:read_file_1ee4e9cc", "arguments": {"path": "C:/Users/wuhan/Desktop/edgeone.md"}}]}',
        "tool_calls": [{
            "id": "file:read_file_1ee4e9cc",
            "type": "function",
            "function": {"name": "file:read_file", "arguments": '{"path": "C:/Users/wuhan/Desktop/edgeone.md"}'}
        }]
    },
    {"role": "tool", "content": "Error: File not found", "tool_call_id": "file:read_file_1ee4e9cc"},
]

resp = httpx.post(
    "https://api.deepseek.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={"model": "deepseek-v4-flash", "messages": messages, "temperature": 0.7},
    timeout=30,
)
print(f"Status: {resp.status_code}")
print(f"Body: {resp.text[:500]}")
