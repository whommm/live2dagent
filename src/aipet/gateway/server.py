"""Gateway WebSocket server entry point."""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets import ServerConnection

from aipet.gateway.canvas import CanvasManager
from aipet.gateway.config import GatewayConfig
from aipet.gateway.events import EventBus
from aipet.gateway.media.audio_player import AudioPlayer
from aipet.gateway.models import Message
from aipet.gateway.providers.ai_echo import EchoProvider
from aipet.gateway.providers.manager import ProviderManager
from aipet.gateway.providers.tts import TTSProvider
from aipet.gateway.providers.tts_edge import EdgeTTSProvider
from aipet.gateway.scheduler import TaskScheduler, set_gateway
from aipet.gateway.session import SessionManager
from aipet.gateway.skills.registry import SkillRegistry
from aipet.gateway.skills.router import ToolRouter
from aipet.gateway.state import AppState, ClientInfo, StateStore
from aipet.utils.paths import ensure_directories, get_config_dir, get_project_root


class Gateway:
    """AIPet Gateway WebSocket server."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig()
        self.bus = EventBus()
        self.store = StateStore(initial_state=AppState(), bus=self.bus)
        self.sessions = SessionManager(bus=self.bus)
        self.provider_manager = ProviderManager()
        self.clients: dict[str, ServerConnection] = {}
        self._pending_req_id: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._live2d_tags = self._scan_live2d_tags()
        self._last_user_activity = time.time()

        # TTS setup
        if self.config.tts_provider == "edge-tts":
            self.tts_provider: TTSProvider = EdgeTTSProvider(self.config.tts_default_voice)
        else:
            self.tts_provider = EdgeTTSProvider(self.config.tts_default_voice)

        self.audio_player = AudioPlayer(
            on_start=lambda path, text, lipsync_data: asyncio.create_task(
                self._broadcast(
                    {
                        "type": "event",
                        "method": "tts.start",
                        "payload": {
                            "session_id": "main",
                            "text": text,
                            "audio_url": path,
                            "lipsync_data": lipsync_data,
                        },
                    }
                )
            ),
            on_end=lambda: asyncio.create_task(
                self._broadcast(
                    {
                        "type": "event",
                        "method": "tts.end",
                        "payload": {"session_id": "main"},
                    }
                )
            ),
        )
        self.audio_player.start()

        # Canvas Manager
        self.canvas = CanvasManager(bus=self.bus)

        # Skill Engine
        self.skills = SkillRegistry()
        self.tool_router = ToolRouter(self.skills)

        # Scheduler
        self.scheduler = TaskScheduler()
        self.scheduler.set_execute_callback(self._on_scheduler_task_completed)

        # Proactive chat
        self.proactive = ProactiveChatService(self)

    async def init(self) -> None:
        """Async initialization: load persisted sessions and messages."""
        await self.sessions.init()
        self.skills.reload()
        await self.store.patch(active_skills=list(self.skills._skills.keys()))
        self._sync_config_to_provider_manager()
        provider = self.provider_manager.get_provider()
        if provider:
            await self.store.patch(
                ai_provider_status=type("obj", (object,), {
                    "name": provider.name,
                    "healthy": True,
                    "last_error": None,
                })()
            )
        # Set default Live2D model for tag injection
        await self.store.patch(current_live2d_model=self.config.live2d_default_model)
        self.proactive.start()
        # Publish gateway reference so skills (scheduler) can reach back
        set_gateway(self)
        self.scheduler.start()

    def _sync_config_to_provider_manager(self) -> None:
        """Push GatewayConfig AI settings into ProviderManager so they take effect."""
        config = self.config
        has_key = bool(config.ai_api_key)
        provider = config.ai_provider
        model = config.ai_model
        print(
            f"[Gateway] Syncing config -> ProviderManager:"
            f" provider={provider}, model={model}, has_key={has_key}"
        )
        if config.ai_provider == "echo" or not config.ai_api_key:
            print("[Gateway] Skipping sync: provider is echo or no api_key")
            return

        from aipet.gateway.providers.manager import ProviderEntry

        target_id = None
        for p in self.provider_manager.providers:
            if p.type == config.ai_provider:
                target_id = p.id
                break

        if target_id is None:
            target_id = config.ai_provider
            entry = ProviderEntry(
                id=target_id,
                name=target_id.capitalize(),
                type=config.ai_provider,
                models=[config.ai_model] if config.ai_model else [],
            )
            self.provider_manager.add_provider(entry)
            print(f"[Gateway] Created new provider entry: {target_id}")

        updates: dict[str, Any] = {}
        updates["api_key"] = config.ai_api_key
        if config.ai_model:
            updates["models"] = [config.ai_model]
        self.provider_manager.update_provider(target_id, **updates)
        print(f"[Gateway] Updated provider {target_id}: model={config.ai_model}")

        if self.provider_manager.current_provider_id != target_id:
            self.provider_manager.set_current(target_id, config.ai_model)
            print(f"[Gateway] Set current provider -> {target_id} / {config.ai_model}")
        elif self.provider_manager.current_model != config.ai_model:
            self.provider_manager.current_model = config.ai_model
            self.provider_manager.save()
            print(f"[Gateway] Updated current model -> {config.ai_model}")
        else:
            print(f"[Gateway] Provider already current: {target_id} / {config.ai_model}")

    def _read_soul(self) -> str:
        """Read the soul.md system prompt."""
        soul_path = get_config_dir() / "soul.md"
        if soul_path.exists():
            return soul_path.read_text(encoding="utf-8")
        return "You are a helpful AI assistant."

    def _read_memory(self) -> str:
        """Read the memory.md long-term memory."""
        memory_path = get_config_dir() / "memory.md"
        if memory_path.exists():
            return memory_path.read_text(encoding="utf-8")
        return ""

    def _scan_live2d_tags(self) -> dict[str, dict[str, list[str]]]:
        """Scan all models for available expressions and motions, grouped by model name."""
        project_models_dir = get_project_root() / "live2dmodels"
        result: dict[str, dict[str, set[str]]] = {}
        if project_models_dir.exists():
            for model_json in project_models_dir.rglob("*.model3.json"):
                model_name = model_json.parent.name
                try:
                    with open(model_json, encoding="utf-8") as f:
                        config = json.load(f)
                    files = config.get("FileReferences", {})
                    expressions: set[str] = set()
                    motions: set[str] = set()
                    for expr in files.get("Expressions", []):
                        name = expr.get("Name", "") if isinstance(expr, dict) else ""
                        if name:
                            expressions.add(name)
                    for group_name in files.get("Motions", {}):
                        if group_name:
                            motions.add(group_name)
                    result[model_name] = {
                        "expressions": expressions,
                        "motions": motions,
                    }
                except Exception:
                    continue
        return {
            name: {"expressions": sorted(data["expressions"]), "motions": sorted(data["motions"])}
            for name, data in result.items()
        }

    def _build_live2d_tag_prompt(self) -> str:
        """Build the Live2D tag instruction for system prompt based on current model."""
        current_model = self.store.state.current_live2d_model
        tags = None
        if current_model and current_model in self._live2d_tags:
            tags = self._live2d_tags[current_model]
        elif self._live2d_tags:
            # fallback to first available model
            tags = next(iter(self._live2d_tags.values()))
        if not tags:
            return ""
        exprs = tags.get("expressions", [])
        motions = tags.get("motions", [])
        if not exprs and not motions:
            return ""
        lines = ["# Live2D Expression & Motion Tags"]
        lines.append(
            "You can control the Live2D pet's expression and motion"
            " by including special tags at the very end of your response."
        )
        if exprs:
            lines.append(f"Available expressions: {', '.join(exprs)}")
        if motions:
            lines.append(f"Available motions: {', '.join(motions)}")
        lines.append(
            "Usage: put tags like [expression:happy] [motion:Tap] at the end of your response."
        )
        lines.append("Only use tags from the available lists. Do not invent new ones.")
        return "\n".join(lines)

    @staticmethod
    def _parse_live2d_tags(text: str) -> tuple[str, list[dict[str, str]]]:
        """Extract [expression:xxx] and [motion:xxx] tags from text.

        Supports optional spaces inside brackets, e.g. [expression: happy].
        Returns (cleaned_text, tags).
        """
        import re
        tag_re = re.compile(r"\[\s*(expression|motion)\s*:\s*([^\[\]]+?)\s*\]")
        tags: list[dict[str, str]] = []
        for match in tag_re.finditer(text):
            tag_type = match.group(1)
            name = match.group(2).strip()
            if name:
                tags.append({"type": tag_type, "name": name})
        cleaned = tag_re.sub("", text).strip()
        return cleaned, tags

    async def _emit_live2d_tags(self, tags: list[dict[str, str]]) -> None:
        """Broadcast live2d expression/motion events to all clients."""
        for tag in tags:
            if tag["type"] == "expression":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.expression",
                        "payload": {"expression": tag["name"]},
                    }
                )
            elif tag["type"] == "motion":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.motion",
                        "payload": {"motion": tag["name"], "priority": 3},
                    }
                )

    async def _play_tts(self, text: str) -> None:
        """Synthesize and enqueue TTS playback."""
        if not self.config.tts_auto_play or not text.strip():
            return
        # Strip any live2d tags as a safety net before TTS
        cleaned_text, _ = self._parse_live2d_tags(text)
        if not cleaned_text.strip():
            return
        try:
            audio_path = await self.tts_provider.synthesize(cleaned_text)
            await self.audio_player.enqueue(audio_path, cleaned_text)
        except Exception as exc:
            print(f"TTS error: {exc}")

    def _build_ai_messages(
        self, session_id: str, include_tools: bool = True, include_live2d_tags: bool = False
    ) -> list[Any]:
        """Build the message list for the AI provider from session context."""
        from aipet.gateway.providers.ai import Message as AIMessage

        soul = self._read_soul()
        memory = self._read_memory()
        system_parts = [f"# Soul\n{soul}"]
        if memory.strip():
            system_parts.append(f"# Memory\n{memory}")

        if include_tools:
            briefs = self.skills.list_tools_brief()
            if briefs:
                tool_desc = (
                    "You have access to the following tools. "
                    "When you decide to use a tool, first call system:get_tool_schema to get its full parameter definition, "
                    "then call the actual tool with the correct arguments.\n\n"
                )
                for b in briefs:
                    tool_desc += f"- {b['name']} — {b['brief']}\n"
                tool_desc += "\n"
                tool_desc += (
                    "Meta-tool (always available):\n"
                    "Tool: system:get_tool_schema\n"
                    "Description: 获取指定工具的完整参数定义。当你决定调用某个工具但不知道具体参数格式时，先调用此工具获取参数详情。\n"
                    "Parameters:\n"
                    '  - tool_name: string (required) — 工具全名，格式为 skill_id:tool_name，如 weather:get_weather\n'
                    "\n"
                    "When you need to use one or more tools, you MUST respond with ONLY a JSON object "
                    'in this exact format, with no other text before or after:\n'
                    '{"tool_calls": [{"name": "skill_id:tool_name", "arguments": {"param": "value"}}]}\n\n'
                    "Example flow:\n"
                    '  User: "北京天气怎么样？"\n'
                    '  Assistant: {"tool_calls": [{"name": "system:get_tool_schema", "arguments": {"tool_name": "weather:get_weather"}}]}\n'
                    '  (schema returned)\n'
                    '  Assistant: {"tool_calls": [{"name": "weather:get_weather", "arguments": {"city": "北京"}}]}\n\n'
                    "If no tool is needed, respond normally in plain text. Do NOT output JSON if no tool is needed."
                )
                # Inject skill authoring guide when skill_writer is available
                if "skill_writer" in self.skills._skills:
                    tool_desc += (
                        "\n\n# Skill Authoring Guide\n"
                        "If the user asks you to create a new skill / tool / plugin, follow this exact workflow:\n"
                        "1. Call skill_writer:create_skill to write the draft code.\n"
                        "2. Call skill_tester:test_skill to validate it.\n"
                        "3. If tests fail, analyze errors and call skill_writer:update_skill to fix, then test again.\n"
                        "4. Repeat steps 2-3 until skill_tester:test_skill returns passed=true.\n"
                        "5. Only then call skill_writer:install_skill to activate the skill.\n\n"
                        "Code constraints for new skills:\n"
                        "- Allowed libraries: Python stdlib + httpx + pydantic only.\n"
                        "- Forbidden: os, subprocess, sys, eval, exec, compile, __import__.\n"
                        "- Every tool function must have type hints and a docstring.\n"
                        "- __init__.py must expose `tools = {\"name\": function, ...}`."
                    )
                system_parts.append(f"# Tools\n{tool_desc}")

        if include_live2d_tags:
            live2d_prompt = self._build_live2d_tag_prompt()
            if live2d_prompt:
                system_parts.append(live2d_prompt)

        messages: list[Any] = [AIMessage(role="system", content="\n\n".join(system_parts))]

        session = self.sessions.get(session_id)
        if session:
            for msg in session.messages:
                messages.append(
                    AIMessage(
                        role=msg.role,
                        content=msg.content,
                        tool_calls=msg.tool_calls,
                        tool_call_id=msg.tool_call_id,
                    )
                )

        # Debug: print FULL messages sent to LLM (no truncation)
        print("\n" + "=" * 70)
        print(f"[LLM REQUEST] session={session_id}, tools={'yes' if include_tools else 'no'}, count={len(messages)}")
        for i, msg in enumerate(messages):
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
            print(f"  --- [{i}] {role.upper()} ---")
            print(content)
            tc = getattr(msg, "tool_calls", None)
            if tc:
                print(f"  [tool_calls]: {json.dumps(tc, ensure_ascii=False)}")
        print("=" * 70)

        return messages

    def _parse_tool_calls(self, text: str) -> list[dict[str, Any]] | None:
        """Parse tool_calls JSON from AI response text.

        Handles markdown code blocks, surrounding text, and nested JSON.
        """
        text = text.strip()
        if not text:
            return None

        def _try_parse(s: str) -> list[dict[str, Any]] | None:
            s = s.strip()
            if not s:
                return None
            try:
                data = json.loads(s)
                if isinstance(data, dict) and "tool_calls" in data:
                    return data["tool_calls"]
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
            return None

        # 1. Strip markdown code blocks
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            result = _try_parse("\n".join(lines))
            if result is not None:
                return result

        # 2. Direct parse
        result = _try_parse(text)
        if result is not None:
            return result

        # 3. Extract JSON by brace/bracket matching
        import re

        for start_char, end_char in [("{", "}"), ("[", "]")]:
            for match in re.finditer(re.escape(start_char), text):
                start = match.start()
                depth = 1
                for i in range(start + 1, len(text)):
                    if text[i] == start_char:
                        depth += 1
                    elif text[i] == end_char:
                        depth -= 1
                        if depth == 0:
                            result = _try_parse(text[start : i + 1])
                            if result is not None:
                                return result
                            break

        return None

    async def _resolve_tool_calls(
        self,
        session_id: str,
        initial_text: str,
        message_id: str | None = None,
        raw_tool_calls: list[dict[str, Any]] | None = None,
    ) -> Message:
        """If the AI response contains tool calls, execute them and get the final reply."""

        ai_provider = self.provider_manager.create_ai_provider()
        # Force text-mode: never pass native tool schemas to the provider
        tools = None
        full_text = initial_text.strip()

        parsed = raw_tool_calls if raw_tool_calls is not None else self._parse_tool_calls(full_text)

        print(f"\n[TOOL RESOLVE] Enter. initial_text={full_text[:200]!r}")
        print(f"[TOOL RESOLVE] parsed={parsed}")

        for loop_idx in range(5):
            if not parsed:
                print(f"[TOOL RESOLVE] Loop {loop_idx}: no parsed tool_calls, breaking")
                break

            print(f"\n[TOOL RESOLVE] Loop {loop_idx}: executing {len(parsed)} tool_call(s)")

            # Serialize tool_calls into content so the model can see its own
            # previous tool calls in the conversation history.
            tool_calls_json = json.dumps({"tool_calls": parsed}, ensure_ascii=False)
            assistant_msg = Message(role="assistant", content=tool_calls_json, tool_calls=parsed)
            await self.sessions.add_message(session_id, assistant_msg)

            for tc in parsed:
                name = tc.get("name", "")
                args = tc.get("arguments", {})
                call_id = tc.get("id") or name
                print(f"[TOOL EXEC] name={name}, args={args}")
                # Broadcast tool start
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "tool.start",
                        "payload": {
                            "session_id": session_id,
                            "tool_call_id": call_id,
                            "name": name,
                            "arguments": args,
                        },
                    }
                )
                # Intercept canvas tools to render them immediately
                try:
                    if name == "system:get_tool_schema":
                        result = self._get_tool_schema_json(args.get("tool_name", ""))
                    elif name.startswith("canvas:"):
                        result = await self._handle_canvas_tool(name, args)
                    else:
                        result = await self.tool_router.call(name, args)
                    print(f"[TOOL RESULT] {name} -> {result[:300] if isinstance(result, str) and len(result) > 300 else result!r}")
                    # Broadcast tool success
                    await self._broadcast(
                        {
                            "type": "event",
                            "method": "tool.result",
                            "payload": {
                                "session_id": session_id,
                                "tool_call_id": call_id,
                                "name": name,
                                "result": result,
                            },
                        }
                    )
                except Exception as exc:
                    result = f"Error executing tool: {exc}"
                    print(f"[TOOL ERROR] {name} -> {result}")
                    # Broadcast tool error
                    await self._broadcast(
                        {
                            "type": "event",
                            "method": "tool.error",
                            "payload": {
                                "session_id": session_id,
                                "tool_call_id": call_id,
                                "name": name,
                                "error": str(exc),
                            },
                        }
                    )
                tool_msg = Message(
                    role="tool",
                    content=result,
                    tool_call_id=call_id,
                )
                await self.sessions.add_message(session_id, tool_msg)

            print(f"[TOOL RESOLVE] Loop {loop_idx}: re-querying AI (include_tools=True)")
            ai_messages = self._build_ai_messages(session_id, include_tools=True)
            full_text = ""
            next_raw_tool_calls: list[dict[str, Any]] | None = None
            async for chunk in ai_provider.chat(ai_messages, tools=tools):
                full_text += chunk.delta
                if getattr(chunk, "tool_calls", None):
                    next_raw_tool_calls = chunk.tool_calls

            print(f"[TOOL RESOLVE] Loop {loop_idx}: AI replied -> {full_text[:300]!r}")
            print(f"[TOOL RESOLVE] Loop {loop_idx}: raw_tool_calls={next_raw_tool_calls}")

            parsed = next_raw_tool_calls if next_raw_tool_calls is not None else self._parse_tool_calls(full_text)
            print(f"[TOOL RESOLVE] Loop {loop_idx}: next parsed={parsed}")

        print(f"\n[TOOL RESOLVE] Done. final content={full_text[:300]!r}\n")

        return Message(
            id=message_id or str(uuid.uuid4()), role="assistant", content=full_text.strip()
        )

    def _get_tool_schema_json(self, tool_name: str) -> str:
        """Return the full JSON schema for a tool."""
        import json

        if ":" not in tool_name:
            return json.dumps(
                {"error": f"Invalid tool name '{tool_name}'. Expected 'skill_id:tool_name'."},
                ensure_ascii=False,
            )
        result = self.skills.get_tool(tool_name)
        if result is None:
            return json.dumps({"error": f"Tool '{tool_name}' not found."}, ensure_ascii=False)
        func, skill = result
        from aipet.gateway.skills.registry import _build_function_schema

        schema = _build_function_schema(
            skill.skill_id, tool_name.split(":", 1)[1], func, skill.description
        )
        return json.dumps(schema, ensure_ascii=False, indent=2)

    async def _handle_canvas_tool(self, name: str, args: dict[str, Any]) -> str:
        """Handle canvas:* tool calls by creating canvas elements and broadcasting."""
        import json

        tool_name = name.split(":", 1)[1]
        if tool_name == "close":
            canvas_id = args.get("canvas_id", "")
            self.canvas.close(canvas_id)
            await self._broadcast(
                {
                    "type": "event",
                    "method": "canvas.close",
                    "payload": {"canvas_id": canvas_id},
                }
            )
            return json.dumps({"status": "closed", "canvas_id": canvas_id}, ensure_ascii=False)

        # Build canvas payload from tool args
        canvas_type = args.get("canvas_type", "bubble")
        if tool_name == "show_bubble":
            canvas_type = "bubble"
        elif tool_name == "show_card":
            canvas_type = "card"
        elif tool_name == "show_image":
            canvas_type = "image"
        elif tool_name == "show_list":
            canvas_type = "list"
        elif tool_name == "show_code":
            canvas_type = "code"

        data = args.get("data", {})
        if not data:
            # AI passes canvas tool arguments flat (e.g. title, content, items);
            # treat the whole args dict as data so the frontend can read content.
            data = dict(args)
        title = args.get("title", "") or data.get("title", "")
        element = self.canvas.create(
            canvas_type=canvas_type,
            data=data,
            title=title,
            duration_ms=args.get("duration_ms", 0),
            width=args.get("width", 280),
            click_action="open_chat" if canvas_type == "bubble" else "none",
        )
        await self._broadcast(
            {
                "type": "event",
                "method": "canvas.show",
                "payload": self.canvas.to_dict(element),
            }
        )
        return json.dumps({"status": "shown", "canvas_id": element.canvas_id}, ensure_ascii=False)

    async def handle_client(self, websocket: ServerConnection) -> None:
        """Handle a single client connection."""
        client_id = str(uuid.uuid4())
        async with self._lock:
            self.clients[client_id] = websocket
        print(f"[Gateway] Client connected: {client_id} ({websocket.remote_address})")

        try:
            async for message in websocket:
                await self._process_message(client_id, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            async with self._lock:
                self.clients.pop(client_id, None)
                self._pending_req_id.pop(client_id, None)
            await self.store.update(
                lambda s: s.model_copy(
                    update={"clients": {k: v for k, v in s.clients.items() if k != client_id}}
                )
            )
            print(f"[Gateway] Client disconnected: {client_id}")

    async def _process_message(self, client_id: str, raw_message: str | bytes) -> None:
        """Parse and dispatch incoming client messages."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            await self._send_error(client_id, "Invalid JSON")
            return

        req_id = data.get("id")
        if req_id:
            async with self._lock:
                self._pending_req_id[client_id] = req_id
        method = data.get("method")
        payload = data.get("payload", {})

        handlers = {
            "client.hello": self._handle_hello,
            "chat.send": self._handle_chat_send,
            "chat.stream": self._handle_chat_stream,
            "chat.history": self._handle_chat_history,
            "session.create": self._handle_session_create,
            "session.list": self._handle_session_list,
            "session.delete": self._handle_session_delete,
            "session.set_current": self._handle_session_set_current,
            "session.rename": self._handle_session_rename,
            "chat.clear": self._handle_chat_clear,
            "canvas.show": self._handle_canvas_show,
            "canvas.close": self._handle_canvas_close,
            "canvas.list": self._handle_canvas_list,
            "canvas.update": self._handle_canvas_update,
            "provider.list": self._handle_provider_list,
            "provider.get_current": self._handle_provider_get_current,
            "provider.set_current": self._handle_provider_set_current,
            "provider.add": self._handle_provider_add,
            "provider.update": self._handle_provider_update,
            "provider.remove": self._handle_provider_remove,
            "live2d.set_model": self._handle_live2d_set_model,
            "system.shutdown": self._handle_shutdown,
        }

        handler = handlers.get(method)
        if handler is None:
            await self._send_error(client_id, f"Unknown method: {method}")
            return

        try:
            await handler(client_id, payload)
        except Exception as exc:
            await self._send_error(client_id, f"Internal error: {exc}")
        finally:
            async with self._lock:
                self._pending_req_id.pop(client_id, None)

    async def _handle_hello(self, client_id: str, payload: dict[str, Any]) -> None:
        """Handle client handshake."""
        client_type = payload.get("client_type", "unknown")
        version = payload.get("version", "")
        client_info = ClientInfo(
            client_id=client_id,
            client_type=client_type,
            connected_at=datetime.now(UTC),
            version=version,
        )
        await self.store.update(
            lambda s: s.model_copy(update={"clients": {**s.clients, client_id: client_info}})
        )
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "client.hello",
                "payload": {
                    "gateway_version": self.store.state.gateway_version,
                    "client_id": client_id,
                },
            },
        )

    async def _ensure_session(self, session_id: str | None) -> Any:
        """Get or create a session by id."""
        sid = session_id or "main"
        session = self.sessions.get(sid)
        if session is None:
            session = self.sessions.create(sid, name="New Session")
            await self.sessions.save_session(session)
        return session

    async def _maybe_auto_rename_session(self, session: Any, content: str) -> None:
        """Auto-rename session based on first user message."""
        if session.name != "New Session":
            return
        title = content.strip().replace("\n", " ").replace("\r", " ")[:20]
        if title:
            await self.sessions.rename(session.id, title)

    @staticmethod
    def _augment_content_with_attachments(content: str, attachments: list[str]) -> str:
        """Append file path hints so the AI knows which files it can read."""
        if not attachments:
            return content
        file_hints = "\n".join(f"[Attached file] {path}" for path in attachments)
        if content.strip():
            return f"{content.strip()}\n\n{file_hints}"
        return file_hints

    async def _handle_chat_send(self, client_id: str, payload: dict[str, Any]) -> None:
        """Handle non-streaming chat message."""
        self._last_user_activity = time.time()
        session_id = payload.get("session_id", "main")
        content = payload.get("content", "")
        attachments: list[str] = payload.get("attachments", [])
        if not content.strip() and not attachments:
            await self._send_error(client_id, "Content cannot be empty")
            return
        if len(content) > 8192:
            await self._send_error(client_id, "Content too long (max 8192 characters)")
            return

        session = await self._ensure_session(session_id)
        augmented_content = self._augment_content_with_attachments(content, attachments)
        user_msg = Message(role="user", content=augmented_content, attachments=attachments)
        await self.sessions.add_message(session.id, user_msg)
        await self._maybe_auto_rename_session(session, content)

        ai_provider = self.provider_manager.create_ai_provider()
        current_entry = self.provider_manager.get_provider()
        if isinstance(ai_provider, EchoProvider) and current_entry and current_entry.type != "echo":
            await self._send_error(
                client_id,
                f"Provider '{current_entry.name}' is missing API key. Falling back to Echo.",
            )
        # Force text-mode two-phase tool calling for all providers
        use_native_tools = False
        ai_messages = self._build_ai_messages(session.id, include_tools=True)
        tools = None
        full_text = ""
        raw_tool_calls: list[dict[str, Any]] | None = None
        async for chunk in ai_provider.chat(ai_messages, tools=tools):
            full_text += chunk.delta
            if getattr(chunk, "tool_calls", None):
                raw_tool_calls = chunk.tool_calls

        assistant_msg = await self._resolve_tool_calls(session.id, full_text, raw_tool_calls=raw_tool_calls)
        cleaned_text, live2d_tags = self._parse_live2d_tags(assistant_msg.content)
        if cleaned_text != assistant_msg.content:
            assistant_msg.content = cleaned_text
        await self.sessions.add_message(session.id, assistant_msg)
        await self._emit_live2d_tags(live2d_tags)

        await self._broadcast(
            {
                "type": "event",
                "method": "chat.message",
                "payload": {
                    "session_id": session.id,
                    "message_id": assistant_msg.id,
                    "role": "assistant",
                    "content": assistant_msg.content,
                    "timestamp": assistant_msg.created_at.isoformat(),
                },
            }
        )
        await self._play_tts(assistant_msg.content)

    async def _handle_chat_stream(self, client_id: str, payload: dict[str, Any]) -> None:
        """Handle streaming chat message."""
        self._last_user_activity = time.time()
        session_id = payload.get("session_id", "main")
        content = payload.get("content", "")
        attachments: list[str] = payload.get("attachments", [])
        if not content.strip() and not attachments:
            await self._send_error(client_id, "Content cannot be empty")
            return
        if len(content) > 8192:
            await self._send_error(client_id, "Content too long (max 8192 characters)")
            return

        session = await self._ensure_session(session_id)
        augmented_content = self._augment_content_with_attachments(content, attachments)
        user_msg = Message(role="user", content=augmented_content, attachments=attachments)
        await self.sessions.add_message(session.id, user_msg)
        await self._maybe_auto_rename_session(session, content)

        print(f"\n[CHAT STREAM] User: {content!r}")

        assistant_msg = Message(role="assistant", content="")
        msg_id = assistant_msg.id

        await self._broadcast(
            {
                "type": "event",
                "method": "chat.stream.start",
                "payload": {"session_id": session.id, "message_id": msg_id},
            }
        )

        ai_provider = self.provider_manager.create_ai_provider()
        current_entry = self.provider_manager.get_provider()
        if isinstance(ai_provider, EchoProvider) and current_entry and current_entry.type != "echo":
            await self._send_error(
                client_id,
                f"Provider '{current_entry.name}' is missing API key. Falling back to Echo.",
            )
        # Force ALL providers to use text-mode two-phase tool calling.
        # This ensures every model follows: brief -> system:get_tool_schema -> actual tool.
        use_native_tools = False
        ai_messages = self._build_ai_messages(session.id, include_tools=True)
        tools = None
        full_text = ""
        raw_tool_calls: list[dict[str, Any]] | None = None
        async for chunk in ai_provider.chat(ai_messages, tools=tools):
            full_text += chunk.delta
            if chunk.delta:
                print(f"[CHAT STREAM] chunk: {chunk.delta!r}")
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "chat.stream.chunk",
                        "payload": {
                            "session_id": session.id,
                            "message_id": msg_id,
                            "delta": chunk.delta,
                        },
                    }
                )
            if getattr(chunk, "tool_calls", None):
                raw_tool_calls = chunk.tool_calls
                print(f"[CHAT STREAM] native tool_calls: {raw_tool_calls}")

        await self._broadcast(
            {
                "type": "event",
                "method": "chat.stream.end",
                "payload": {
                    "session_id": session.id,
                    "message_id": msg_id,
                    "finish_reason": "stop",
                },
            }
        )

        final_msg = await self._resolve_tool_calls(session.id, full_text.strip(), message_id=msg_id, raw_tool_calls=raw_tool_calls)
        cleaned_text, live2d_tags = self._parse_live2d_tags(final_msg.content)
        if cleaned_text != final_msg.content:
            final_msg.content = cleaned_text
        await self._emit_live2d_tags(live2d_tags)
        print(f"[CHAT STREAM] Final message: {final_msg.content[:300]!r}")
        if final_msg.content or final_msg.tool_calls:
            await self.sessions.add_message(session.id, final_msg)
            # Always broadcast final cleaned text to replace streamed content (including tags)
            await self._broadcast(
                {
                    "type": "event",
                    "method": "chat.message",
                    "payload": {
                        "session_id": session.id,
                        "message_id": final_msg.id,
                        "role": "assistant",
                        "content": final_msg.content,
                        "timestamp": final_msg.created_at.isoformat(),
                    },
                }
            )
            await self._play_tts(final_msg.content)
        else:
            await self._play_tts(full_text.strip())

    async def _handle_chat_history(self, client_id: str, payload: dict[str, Any]) -> None:
        """Return chat history for a session."""
        session_id = payload.get("session_id", "main")
        limit = payload.get("limit", 100)
        if not isinstance(limit, int) or limit <= 0 or limit > 1000:
            limit = 100
        session = self.sessions.get(session_id)
        messages: list[dict[str, Any]] = []
        if session:
            msgs = session.messages[-limit:] if limit else session.messages
            # Filter out intermediate tool-call JSON messages so the frontend
            # doesn't render raw JSON as chat bubbles.
            messages = [
                m.model_dump(mode="json")
                for m in msgs
                if not (m.role == "assistant" and m.tool_calls)
            ]
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "chat.history",
                "payload": {
                    "session_id": session_id,
                    "messages": messages,
                },
            },
        )

    async def _handle_session_create(self, client_id: str, payload: dict[str, Any]) -> None:
        """Create a new session."""
        name = payload.get("name", "New Session")
        soul_path = payload.get("soul_path", "soul.md")
        session = self.sessions.create(name=name, soul_path=soul_path)
        await self.sessions.save_session(session)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "session.create",
                "payload": {
                    "session": session.model_dump(mode="json"),
                },
            },
        )

    async def _handle_session_list(self, client_id: str, payload: dict[str, Any]) -> None:
        """List all sessions."""
        sessions = self.sessions.list_sessions()
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "session.list",
                "payload": {
                    "sessions": [s.model_dump(mode="json") for s in sessions],
                },
            },
        )

    async def _handle_session_delete(self, client_id: str, payload: dict[str, Any]) -> None:
        """Delete a session."""
        session_id = payload.get("session_id", "")
        ok = await self.sessions.delete(session_id)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "session.delete",
                "payload": {"success": ok, "session_id": session_id},
            },
        )

    async def _handle_session_set_current(self, client_id: str, payload: dict[str, Any]) -> None:
        """Set the current active session."""
        session_id = payload.get("session_id", "")
        await self.store.patch(current_session_id=session_id)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "session.set_current",
                "payload": {"success": True, "current_session_id": session_id},
            },
        )

    async def _handle_session_rename(self, client_id: str, payload: dict[str, Any]) -> None:
        """Rename a session."""
        session_id = payload.get("session_id", "")
        name = payload.get("name", "")
        ok = await self.sessions.rename(session_id, name)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "session.rename",
                "payload": {"success": ok, "session_id": session_id, "name": name},
            },
        )

    async def _handle_chat_clear(self, client_id: str, payload: dict[str, Any]) -> None:
        """Clear all messages in a session."""
        session_id = payload.get("session_id", "")
        ok = await self.sessions.clear_messages(session_id)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "chat.clear",
                "payload": {"success": ok, "session_id": session_id},
            },
        )

    # ------------------------------------------------------------------
    # Canvas handlers
    # ------------------------------------------------------------------

    async def _handle_canvas_show(self, client_id: str, payload: dict[str, Any]) -> None:
        """Create and broadcast a new canvas element."""
        element = self.canvas.create(
            canvas_type=payload.get("canvas_type", "bubble"),
            data=payload.get("data", {}),
            title=payload.get("title", ""),
            position=payload.get("position", "head"),
            x=payload.get("x"),
            y=payload.get("y"),
            width=payload.get("width", 280),
            height=payload.get("height", 0),
            duration_ms=payload.get("duration_ms", 0),
            click_action=payload.get("click_action", "dismiss"),
            style=payload.get("style", {}),
            canvas_id=payload.get("canvas_id"),
        )
        await self._broadcast(
            {
                "type": "event",
                "method": "canvas.show",
                "payload": self.canvas.to_dict(element),
            }
        )
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "canvas.show",
                "payload": {"success": True, "canvas_id": element.canvas_id},
            },
        )

    async def _handle_canvas_close(self, client_id: str, payload: dict[str, Any]) -> None:
        """Close a canvas by ID."""
        canvas_id = payload.get("canvas_id", "")
        ok = self.canvas.close(canvas_id)
        await self._broadcast(
            {
                "type": "event",
                "method": "canvas.close",
                "payload": {"canvas_id": canvas_id},
            }
        )
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "canvas.close",
                "payload": {"success": ok, "canvas_id": canvas_id},
            },
        )

    async def _handle_canvas_list(self, client_id: str, payload: dict[str, Any]) -> None:
        """List all active canvases."""
        canvases = [self.canvas.to_dict(c) for c in self.canvas.list_canvases()]
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "canvas.list",
                "payload": {"canvases": canvases},
            },
        )

    async def _handle_canvas_update(self, client_id: str, payload: dict[str, Any]) -> None:
        """Update an existing canvas's data."""
        canvas_id = payload.get("canvas_id", "")
        data = payload.get("data", {})
        ok = self.canvas.update_data(canvas_id, data)
        element = self.canvas.get(canvas_id)
        if element and ok:
            await self._broadcast(
                {
                    "type": "event",
                    "method": "canvas.update",
                    "payload": self.canvas.to_dict(element),
                }
            )
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "canvas.update",
                "payload": {"success": ok, "canvas_id": canvas_id},
            },
        )

    # ------------------------------------------------------------------
    # Provider management handlers
    # ------------------------------------------------------------------

    async def _handle_provider_list(self, client_id: str, payload: dict[str, Any]) -> None:
        providers = self.provider_manager.list_providers()
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.list",
                "payload": {
                    "providers": [p.model_dump(mode="json") for p in providers],
                },
            },
        )

    async def _handle_provider_get_current(self, client_id: str, payload: dict[str, Any]) -> None:
        entry = self.provider_manager.get_provider()
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.get_current",
                "payload": {
                    "provider": entry.model_dump(mode="json") if entry else None,
                    "current_provider_id": self.provider_manager.current_provider_id,
                    "current_model": self.provider_manager.current_model,
                },
            },
        )

    async def _handle_provider_set_current(self, client_id: str, payload: dict[str, Any]) -> None:
        pid = payload.get("provider_id", "")
        model = payload.get("model")
        ok = self.provider_manager.set_current(pid, model)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.set_current",
                "payload": {"success": ok},
            },
        )

    async def _handle_provider_add(self, client_id: str, payload: dict[str, Any]) -> None:
        from aipet.gateway.providers.manager import ProviderEntry

        entry = ProviderEntry.model_validate(payload.get("provider", {}))
        ok = self.provider_manager.add_provider(entry)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.add",
                "payload": {"success": ok},
            },
        )

    async def _handle_provider_update(self, client_id: str, payload: dict[str, Any]) -> None:
        pid = payload.get("provider_id", "")
        updates = payload.get("updates", {})
        ok = self.provider_manager.update_provider(pid, **updates)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.update",
                "payload": {"success": ok},
            },
        )

    async def _handle_provider_remove(self, client_id: str, payload: dict[str, Any]) -> None:
        pid = payload.get("provider_id", "")
        ok = self.provider_manager.remove_provider(pid)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.remove",
                "payload": {"success": ok},
            },
        )

    async def _handle_live2d_set_model(self, client_id: str, payload: dict[str, Any]) -> None:
        """Update the current Live2D model so prompts can inject correct tags."""
        model_name = payload.get("model_name", "")
        await self.store.patch(current_live2d_model=model_name)
        print(f"[Gateway] Live2D model set to: {model_name}")
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "live2d.set_model",
                "payload": {"success": True, "model_name": model_name},
            },
        )

    async def _handle_shutdown(self, client_id: str, payload: dict[str, Any]) -> None:
        """Graceful shutdown request."""
        await self._broadcast(
            {"type": "event", "method": "system.shutdown", "payload": {}}
        )
        await self.proactive.stop()
        await self.audio_player.stop()
        loop = asyncio.get_running_loop()
        # Cancel all running tasks except the current one
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.stop()

    async def _send(self, client_id: str, data: dict[str, Any]) -> None:
        """Send a message to a specific client."""
        async with self._lock:
            client = self.clients.get(client_id)
            req_id = self._pending_req_id.get(client_id)
        if client is None:
            return
        if req_id is not None and "id" not in data:
            data = {**data, "id": req_id}
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            await client.send(json.dumps(data))

    async def _send_error(self, client_id: str, message: str) -> None:
        """Send an error message to a client."""
        await self._send(
            client_id,
            {
                "type": "event",
                "method": "system.error",
                "payload": {"code": "ERROR", "message": message},
            },
        )

    async def _broadcast(self, data: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        async with self._lock:
            clients_snapshot = list(self.clients.values())
        if clients_snapshot:
            await asyncio.gather(
                *[client.send(json.dumps(data)) for client in clients_snapshot],
                return_exceptions=True,
            )

    def _on_scheduler_task_completed(self, task: Any, result_text: str) -> None:
        """Callback invoked after each scheduled task execution.

        Broadcasts the result to all connected clients.
        """
        asyncio.create_task(
            self._broadcast(
                {
                    "type": "event",
                    "method": "scheduler.task.completed",
                    "payload": {
                        "task_id": task.id,
                        "name": task.name,
                        "skill_id": task.skill_id,
                        "tool_name": task.tool_name,
                        "result": result_text,
                        "runs_completed": task.runs_completed,
                        "active": task.active,
                    },
                }
            )
        )

    async def run(self) -> None:
        bind = self.config.gateway_bind
        port = self.config.gateway_port
        print(f"[Gateway] Starting server on ws://{bind}:{port}")
        async with websockets.serve(self.handle_client, bind, port):
            await asyncio.Future()  # run forever


class ProactiveChatService:
    """Background service that periodically triggers proactive AI messages."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self._task: asyncio.Task[Any] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        """Start the background loop."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Signal the background loop to stop and wait for it."""
        self._stopped.set()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        """Main loop: wait random interval, then trigger a proactive message."""
        print(f"[ProactiveChat] Loop started. enabled={self.gateway.config.proactive_enabled}")
        while not self._stopped.is_set():
            if not self.gateway.config.proactive_enabled:
                if not getattr(self, "_disabled_logged", False):
                    print("[ProactiveChat] Proactive disabled, waiting...")
                    self._disabled_logged = True
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=5.0)
                except TimeoutError:
                    continue
                return
            else:
                self._disabled_logged = False

            cfg = self.gateway.config
            interval_min = cfg.proactive_interval_min
            interval_max = cfg.proactive_interval_max
            if interval_min > interval_max:
                interval_min, interval_max = interval_max, interval_min

            interval = random.randint(interval_min, interval_max)
            print(f"[ProactiveChat] Next trigger in {interval}s")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                print("[ProactiveChat] Stop signal received, exiting loop")
                return
            except TimeoutError:
                pass

            print("[ProactiveChat] Triggering proactive message...")
            await self._trigger()

    async def _trigger(self) -> None:
        """Generate and broadcast a proactive message."""
        print("[ProactiveChat] _trigger() started")
        if not self.gateway.clients:
            print("[ProactiveChat] No clients connected, skipping")
            return

        # Skip if user was recently active (within 30 seconds)
        if time.time() - self.gateway._last_user_activity < 30:
            print("[ProactiveChat] User is active, skipping proactive message")
            return

        try:
            session_id = self.gateway.store.state.current_session_id or "main"
            session = await self.gateway._ensure_session(session_id)
            print(f"[ProactiveChat] Session ensured: {session.id}")
            from aipet.gateway.providers.ai import Message as AIMessage
            from aipet.gateway.providers.ai_echo import EchoProvider
            from aipet.gateway.providers.factory import create_ai_provider

            ai_provider = self.gateway.provider_manager.create_ai_provider()
            name = ai_provider.name
            model_id = ai_provider.model_id
            print(f"[ProactiveChat] ProviderManager created: {name} / {model_id}")
            # Fallback: if ProviderManager yields Echo but gateway.toml has a real key, use it
            if isinstance(ai_provider, EchoProvider) and self.gateway.config.ai_api_key:
                print(
                    "[ProactiveChat] ProviderManager returned Echo,"
                    " falling back to GatewayConfig provider"
                )
                ai_provider = create_ai_provider(self.gateway.config)
                name = ai_provider.name
                model_id = ai_provider.model_id
                print(f"[ProactiveChat] Fallback provider: {name} / {model_id}")

            # Build messages from session context.
            # Include recent history so the proactive message feels connected
            # rather than abruptly starting a brand-new topic.
            all_messages = self.gateway._build_ai_messages(
                session.id, include_tools=False, include_live2d_tags=False
            )
            messages = []
            if all_messages and all_messages[0].role == "system":
                messages.append(all_messages[0])

            # Add recent user/assistant exchanges (up to 3 rounds = 6 messages)
            recent = [m for m in all_messages[1:] if m.role in ("user", "assistant")]
            messages.extend(recent[-6:])

            now = datetime.now().strftime("%H:%M")
            instruction = (
                f"现在的时间是 {now}。\n"
                "你正在主动找用户搭话。可以自然地延续刚才的话题，"
                "也可以根据氛围开启一个轻松的新话题。"
                "用温柔、自然、简短的 1-2 句话引起用户的注意。"
                "不要使用任何工具。只输出纯文本。必须使用中文回复。"
            )
            if messages and messages[0].role == "system":
                messages[0] = AIMessage(
                    role="system", content=messages[0].content + "\n\n" + instruction
                )
            else:
                messages.insert(0, AIMessage(role="system", content=instruction))
            print(f"[ProactiveChat] Calling AI with {len(messages)} message(s)")
            full_text = ""
            chunk_count = 0
            async for chunk in ai_provider.chat(messages):
                full_text += chunk.delta
                chunk_count += 1
            print(
                f"[ProactiveChat] AI returned {chunk_count} chunk(s),"
                f" raw text length={len(full_text)}"
            )

            content = full_text.strip()
            print(f"[ProactiveChat] Final content: {content!r}")
            if not content:
                print("[ProactiveChat] Empty content, aborting broadcast")
                return

            # Strip live2d tags from proactive messages too
            cleaned_content, live2d_tags = self.gateway._parse_live2d_tags(content)
            if cleaned_content != content:
                content = cleaned_content

            msg = Message(role="assistant", content=content, source="proactive")
            await self.gateway.sessions.add_message(session.id, msg, update_session=False)
            print(f"[ProactiveChat] Message saved to session {session.id}")

            # Broadcast any live2d tags from proactive message
            await self.gateway._emit_live2d_tags(live2d_tags)

            # Pick random expression/motion from current model if available
            current_model = self.gateway.store.state.current_live2d_model or ""
            model_tags = self.gateway._live2d_tags.get(current_model, {})
            expressions = model_tags.get("expressions", [])
            motions = model_tags.get("motions", [])
            expression = random.choice(expressions) if expressions else ""
            motion = random.choice(motions) if motions else ""

            print("[ProactiveChat] Broadcasting chat.proactive event...")
            await self.gateway._broadcast(
                {
                    "type": "event",
                    "method": "chat.proactive",
                    "payload": {
                        "session_id": session.id,
                        "message_id": msg.id,
                        "role": "assistant",
                        "content": content,
                        "timestamp": msg.created_at.isoformat(),
                        "expression": expression,
                        "motion": motion,
                        "source": "proactive",
                    },
                }
            )
            print("[ProactiveChat] Broadcast complete")

            if self.gateway.config.proactive_tts:
                print("[ProactiveChat] Enqueuing TTS...")
                await self.gateway._play_tts(content)
                print("[ProactiveChat] TTS enqueued")
            else:
                print("[ProactiveChat] TTS disabled, skipping")
        except Exception as exc:
            import traceback
            print(f"[ProactiveChat] ERROR: {exc}")
            traceback.print_exc()
            await self.gateway._broadcast(
                {
                    "type": "event",
                    "method": "system.error",
                    "payload": {"code": "PROACTIVE_ERROR", "message": str(exc)},
                }
            )


def main() -> int:
    """Entry point for the Gateway server."""
    ensure_directories()

    async def _main() -> int:
        gateway = Gateway()
        await gateway.init()
        try:
            await gateway.run()
        except KeyboardInterrupt:
            print("\n[Gateway] Shutting down...")
        finally:
            await gateway.proactive.stop()
        await gateway.scheduler.stop()
        return 0

    return asyncio.run(_main())
