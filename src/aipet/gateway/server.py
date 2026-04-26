"""Gateway WebSocket server entry point."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import random
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
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
from aipet.gateway.state import AppState, ClientInfo, ProviderStatus, StateStore
from aipet.gateway.streaming import ToolCallStreamGuard
from aipet.gateway.tool_decision import ToolDecider
from aipet.utils.paths import ensure_directories, get_config_dir, get_project_root

_current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aipet_current_request_id", default=None
)


class Gateway:
    """AIPet Gateway WebSocket server."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self._logger = structlog.get_logger("gateway")
        self.config = config or GatewayConfig()
        self._load_ai_tool_settings()
        self.bus = EventBus()
        self.store = StateStore(initial_state=AppState(), bus=self.bus)
        self.sessions = SessionManager(bus=self.bus)
        self.provider_manager = ProviderManager()
        self.clients: dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()
        self._live2d_tags = self._scan_live2d_tags()
        self._last_user_activity = time.time()
        self._live2d_state: dict[str, Any] = {}

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
            on_error=lambda msg: asyncio.create_task(
                self._broadcast(
                    {
                        "type": "event",
                        "method": "system.error",
                        "payload": {"code": "TTS_PLAYBACK_ERROR", "message": msg},
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

    def _ai_tool_settings_path(self) -> Any:
        return get_config_dir() / "ai_tools.toml"

    def _load_ai_tool_settings(self) -> None:
        path = self._ai_tool_settings_path()
        if not path.exists():
            return
        try:
            import toml

            data = toml.load(path)
        except Exception:
            self._logger.exception("Failed to load AI/tool settings")
            return
        for key in self._runtime_settings_payload_keys():
            if key in data:
                with contextlib.suppress(Exception):
                    setattr(self.config, key, data[key])

    def _save_ai_tool_settings(self) -> None:
        path = self._ai_tool_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import toml

            data = {key: getattr(self.config, key) for key in self._runtime_settings_payload_keys()}
            with open(path, "w", encoding="utf-8") as f:
                toml.dump(data, f)
        except Exception:
            self._logger.exception("Failed to save AI/tool settings")

    @staticmethod
    def _runtime_settings_payload_keys() -> tuple[str, ...]:
        return (
            "tts_auto_play",
            "proactive_enabled",
            "proactive_tts",
            "tool_calling_strategy",
            "tool_context_mode",
            "phase1_max_candidate_tools",
            "phase1_direct_confidence_threshold",
            "max_tool_loops",
            "enable_streaming_guard",
            "tool_debug_events",
            "intent_provider_id",
            "intent_model",
            "tool_provider_id",
            "tool_model",
            "summary_provider_id",
            "summary_model",
            "proactive_provider_id",
            "proactive_model",
        )

    async def init(self) -> None:
        """Async initialization: load persisted sessions and messages."""
        await self.sessions.init()
        self.skills.reload()
        await self.store.patch(active_skills=list(self.skills._skills.keys()))
        self._sync_config_to_provider_manager()
        provider = self.provider_manager.get_provider()
        if provider:
            await self.store.patch(
                ai_provider_status=ProviderStatus(
                    name=provider.name,
                    healthy=True,
                    last_error=None,
                )
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
        self._logger.info(
            "Syncing config to ProviderManager",
            provider=provider,
            model=model,
            has_key=has_key,
        )
        if config.ai_provider == "echo" or not config.ai_api_key:
            self._logger.info("Skipping sync: provider is echo or no api_key")
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
            self._logger.info("Created new provider entry", target_id=target_id)

        updates: dict[str, Any] = {}
        updates["api_key"] = config.ai_api_key
        if config.ai_model:
            updates["models"] = [config.ai_model]
        self.provider_manager.update_provider(target_id, **updates)
        self._logger.info("Updated provider", target_id=target_id, model=config.ai_model)

        if self.provider_manager.current_provider_id != target_id:
            self.provider_manager.set_current(target_id, config.ai_model)
            self._logger.info("Set current provider", target_id=target_id, model=config.ai_model)
        elif self.provider_manager.current_model != config.ai_model:
            self.provider_manager.current_model = config.ai_model
            self.provider_manager.save()
            self._logger.info("Updated current model", model=config.ai_model)
        else:
            self._logger.info(
                "Provider already current", target_id=target_id, model=config.ai_model
            )

    def _create_role_ai_provider(self, role: str) -> Any:
        """Create a provider for a specialized model role.

        Role-specific settings are optional.  When unset or invalid, the role
        follows the current chat model.
        """
        provider_id = getattr(self.config, f"{role}_provider_id", None)
        model = getattr(self.config, f"{role}_model", None)
        get_provider = getattr(self.provider_manager, "get_provider", None)
        if provider_id and callable(get_provider) and get_provider(provider_id):
            return self.provider_manager.create_ai_provider(provider_id, model)
        return self.provider_manager.create_ai_provider()

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
        """Build the Live2D control instruction for system prompt."""
        lines = [
            "# Live2D 身体与表情控制",
            "你可以在回复末尾使用特殊标签来控制紫羽的姿态、表情和造型。",
            "",
            "## Pose（姿态）— 控制头部角度、眼神方向、身体姿势",
            "- [pose:look_at_user] — 正视主人",
            "- [pose:look_away] — 目光飘向别处（害羞/心虚）",
            "- [pose:tilt_left] / [pose:tilt_right] — 歪头杀",
            "- [pose:lean_forward] — 身体前倾，凑近主人",
            "- [pose:lean_back] — 身体后仰",
            "- [pose:look_up] / [pose:look_down] — 抬头/低头",
            "- [pose:nod] — 点头",
            "- [pose:shake_head] — 摇头",
            "- [pose:gaze_left] / [pose:gaze_right] — 眼神瞟向一侧",
            "",
            "## Emotion（表情）— 控制五官情绪",
            "- [emotion:happy] — 开心，笑眼弯起",
            "- [emotion:shy] — 害羞脸红，眼神向下",
            "- [emotion:angry] — 生气皱眉，眼神锐利",
            "- [emotion:sad] — 难过，眼神落寞",
            "- [emotion:cry] — 哭泣 QAQ",
            "- [emotion:confused] — 疑惑歪头",
            "- [emotion:dizzy] — 晕乎乎",
            "- [emotion:excited] — 兴奋星星眼",
            "- [emotion:pout] — 嘟嘴",
            "- [emotion:tease] — 调皮吐舌头",
            "- [emotion:bite_lip] — 咬嘴唇",
            "- [emotion:surprised] — 惊讶睁大眼",
            "- [emotion:calm] — 恢复平静",
            "",
            "## Prop（道具/造型）",
            "- [prop:wings_big] / [prop:wings_small] / [prop:wings_hide] — 大/小/收起翅膀",
            "- [prop:halo_on] / [prop:halo_off] — 头顶光环",
            "- [prop:twintails] / [prop:default_hair] — 双马尾/默认发型",
            "- [prop:pray] / [prop:pray_off] — 双手合十祈祷",
            "- [prop:microphone] / [prop:microphone_off] — 麦克风",
            "- [prop:trail_on] / [prop:trail_off] — 身后拖尾",
            "- [prop:hands_free] / [prop:hands_on_chin] — 双手自然下垂 / 手托下巴",
            "",
            "## 使用规则",
            "- 每次回复最多使用 2-3 个标签",
            "- 标签放在回复最末尾，单独一行或跟在文字后面",
            "- 根据对话情绪和当前姿态自然选择，不要强行堆砌",
            "- 注意你当前的状态，保持动作连贯性（比如已经歪头了就不要再发歪头）",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_live2d_tags(text: str) -> tuple[str, list[dict[str, str]]]:
        """Extract live2d tags from text.

        Supports: [expression:xxx], [motion:xxx], [pose:xxx], [emotion:xxx], [prop:xxx]
        Returns (cleaned_text, tags).
        """
        import re

        tag_re = re.compile(r"\[\s*(expression|motion|pose|emotion|prop)\s*:\s*([^\[\]]+?)\s*\]")
        tags: list[dict[str, str]] = []
        for match in tag_re.finditer(text):
            tag_type = match.group(1)
            name = match.group(2).strip()
            if name:
                tags.append({"type": tag_type, "name": name})
        cleaned = tag_re.sub("", text).strip()
        return cleaned, tags

    async def _emit_live2d_tags(self, tags: list[dict[str, str]]) -> None:
        """Broadcast live2d pose/emotion/prop events to all clients."""
        for tag in tags:
            ttype = tag["type"]
            name = tag["name"]
            if ttype == "expression":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.expression",
                        "payload": {"expression": name},
                    }
                )
            elif ttype == "motion":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.motion",
                        "payload": {"motion": name, "priority": 3},
                    }
                )
            elif ttype == "pose":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.pose",
                        "payload": {"pose": name, "duration_ms": 500},
                    }
                )
            elif ttype == "emotion":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.emotion",
                        "payload": {"emotion": name, "duration_ms": 500},
                    }
                )
            elif ttype == "prop":
                await self._broadcast(
                    {
                        "type": "event",
                        "method": "live2d.prop",
                        "payload": {"prop": name, "duration_ms": 500},
                    }
                )

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        """Clean text for TTS by removing formatting, actions, emoji, etc.

        Ported from Hiyori's ttsPlayer.ts cleanForTTS().
        """
        import re

        # 1. Remove parenthetical action descriptions
        text = re.sub(r"（[^（）]*）", "", text)  # 全角括号
        text = re.sub(r"\([^()]*\)", "", text)  # 半角括号
        text = re.sub(r"【[^【】]*】", "", text)  # 方头括号
        text = re.sub(r"「[^「」]*」", "", text)  # 日式引号
        text = re.sub(r"『[^『』]*』", "", text)  # 日式双引号
        text = re.sub(r"〈[^〈〉]*〉", "", text)  # 尖括号
        text = re.sub(r"《[^《》]*》", "", text)  # 书名号

        # 2. Remove asterisk-wrapped actions (AI common format)
        text = re.sub(r"\*[^*\n]{1,30}\*", "", text)

        # 3. Markdown → plain text
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **bold**
        text = re.sub(r"\*(.+?)\*", r"\1", text)  # *italic*
        text = re.sub(r"`{1,3}[\s\S]*?`{1,3}", "", text)  # `code` / ```block```
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [link](url)
        text = re.sub(r"^#{1,6}\s", "", text, flags=re.MULTILINE)  # headings
        text = re.sub(r"^[-*+]\s", "", text, flags=re.MULTILINE)  # list items
        text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)  # quotes
        text = text.replace("_", "").replace("~", "").replace("|", "")

        # 4. Emoji + kaomoji + decorative symbols → comma separator
        def _emoji_to_comma(m: re.Match) -> str:  # noqa: D401
            return "，"

        # Unicode emoji ranges (simplified but covers most)
        text = re.sub(
            r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
            r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
            r"\U00002702-\U000027B0\U000024C2-\U0001F251"
            r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F"
            r"\U00002600-\U000026FF\U000026A0-\U000026FF]+",
            _emoji_to_comma,
            text,
        )
        # Kaomoji-like sequences
        text = re.sub(r"[（()）≧≦∇OwO><;:XDd^_=+\-~·°▽○●□■♡♥★☆♪♫◇◆]{3,}", "，", text)
        # Loose decorative symbols
        text = re.sub(r"[♪♫♬♩★☆✦✧❤♡♥❥◇◆○●□■△▽→←↑↓↔]", "", text)

        # 5. Clean up excess commas and whitespace
        text = re.sub(r"[，,]{2,}", "，", text)
        text = re.sub(r"([。！？!?…])，", r"\1", text)
        text = re.sub(r"，([。！？!?…])", r"\1", text)
        text = re.sub(r"^\s*[，,]\s*", "", text)
        text = re.sub(r"\s*[，,]\s*$", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    async def _play_tts(self, text: str) -> None:
        """Synthesize and enqueue TTS playback."""
        if not self.config.tts_auto_play or not text.strip():
            return
        # Strip any live2d tags as a safety net before TTS
        cleaned_text, _ = self._parse_live2d_tags(text)
        # Further clean for TTS
        cleaned_text = self._clean_for_tts(cleaned_text)
        if not cleaned_text.strip():
            return
        try:
            audio_path = await self.tts_provider.synthesize(cleaned_text)
            await self.audio_player.enqueue(audio_path, cleaned_text)
        except Exception as exc:
            self._logger.exception("TTS error")
            await self._broadcast(
                {
                    "type": "event",
                    "method": "system.error",
                    "payload": {
                        "code": "TTS_SYNTHESIS_ERROR",
                        "message": f"语音合成失败: {exc}",
                    },
                }
            )

    async def _maybe_compact_session(self, session_id: str) -> None:
        """Compress early messages into a memory summary when context grows too long."""
        session = self.sessions.get(session_id)
        if not session or len(session.messages) < 20:
            return
        from aipet.gateway.providers.ai import Message as AIMessage

        older = session.messages[:-10]
        prompt = "请将以下对话总结为 200 字以内的摘要，保留关键事实和用户信息：\n\n"
        for m in older:
            prompt += f"{m.role}: {m.content}\n"
        try:
            ai_provider = self._create_role_ai_provider("summary")
            summary = ""
            async for chunk in ai_provider.chat([AIMessage(role="user", content=prompt)]):
                summary += chunk.delta
            summary = summary.strip()
            if summary:
                await self.sessions.compact_session(session_id, summary)
                self._logger.info("Session compacted", session_id=session_id, summary=summary[:100])
        except Exception:
            self._logger.exception("Session compaction failed")

    @staticmethod
    def _filter_incomplete_tool_calls(messages: list[Any]) -> list[Any]:
        """Remove assistant messages with unfulfilled tool_calls and orphan tool messages.

        DeepSeek (and other strict providers) require that every assistant message
        with ``tool_calls`` be followed by the same number of ``tool`` role messages
        responding to each ``tool_call_id``. Incomplete sequences cause 400 errors.
        """
        result: list[Any] = []
        skip_until = -1
        for i, msg in enumerate(messages):
            if i <= skip_until:
                continue
            if msg.role == "assistant" and msg.tool_calls:
                expected = len(msg.tool_calls)
                actual = 0
                j = i + 1
                while j < len(messages) and messages[j].role == "tool":
                    actual += 1
                    j += 1
                if actual >= expected:
                    result.append(msg)
                    for k in range(i + 1, j):
                        result.append(messages[k])
                else:
                    skip_until = j - 1
            elif msg.role == "tool":
                # Orphan tool message – skip
                continue
            else:
                result.append(msg)
        return result

    def _build_ai_messages(
        self,
        session_id: str,
        include_tools: bool = True,
        include_live2d_tags: bool = False,
        tool_briefs: list[dict[str, str]] | None = None,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> list[Any]:
        """Build the message list for the AI provider from session context.

        *tool_briefs* allows overriding the full skill list when using
        dynamic tool selection (Phase-1 decision flow).
        """
        from aipet.gateway.providers.ai import Message as AIMessage

        soul = self._read_soul()
        memory = self._read_memory()
        system_parts = [f"# Soul\n{soul}"]
        if memory.strip():
            system_parts.append(f"# Memory\n{memory}")

        session = self.sessions.get(session_id)
        if session and session.memory_summary.strip():
            system_parts.append(f"# Session Summary\n{session.memory_summary}")

        if include_tools:
            briefs = tool_briefs if tool_briefs is not None else self.skills.list_tools_brief()
            if briefs:
                has_full_schemas = bool(tool_schemas)
                tool_desc = "You have access to the following tools.\n\n"
                for b in briefs:
                    tool_desc += f"- {b['name']} — {b['brief']}\n"
                tool_desc += "\n"
                if has_full_schemas:
                    tool_desc += "Full schemas for active tools:\n"
                    for schema in tool_schemas:
                        tool_desc += json.dumps(schema, ensure_ascii=False, indent=2)
                        tool_desc += "\n"
                    tool_desc += "\n"
                    tool_desc += (
                        "The full schemas above are already available. "
                        "When a tool is needed, call the actual tool directly. "
                        "Do not call system:get_tool_schema for these active tools.\n\n"
                    )
                tool_desc += (
                    "--- TOOL CALLING RULES (STRICT) ---\n"
                    "When you need to use one or more tools, you MUST output "
                    "EXACTLY ONE raw JSON object.\n"
                    "NO chitchat, NO greetings, NO explanations, "
                    "NO thinking out loud, NO markdown.\n"
                    "NOT a single character before or after the JSON.\n\n"
                    'Required format: {"tool_calls": [{"name": "skill_id:tool_name", '
                    '"arguments": {"param": "value"}}]}\n\n'
                    "WRONG: '让我查一下～' followed by JSON\n"
                    "WRONG: JSON wrapped in ```code blocks```\n"
                    "WRONG: XML / DSML tags like <｜DSML｜tool_calls>\n"
                    "RIGHT:  Only the raw JSON object itself, nothing else.\n\n"
                    "If no tool is needed, respond normally in plain text. "
                    "Do NOT output JSON if no tool is needed."
                )
                if not has_full_schemas:
                    tool_desc += (
                        "\n\nMeta-tool (always available):\n"
                        "Tool: system:get_tool_schema\n"
                        "Description: Get the full parameter schema for a tool before "
                        "calling it.\n"
                        "Parameters:\n"
                        "  - tool_name: string (required), format skill_id:tool_name, "
                        "for example weather:get_weather\n"
                        "\n"
                        "Example flow:\n"
                        '  User: "北京天气怎么样？"\n'
                        '  Assistant: {"tool_calls": [{"name": "system:get_tool_schema", '
                        '"arguments": {"tool_name": "weather:get_weather"}}]}\n'
                        "  (schema returned)\n"
                        '  Assistant: {"tool_calls": [{"name": "weather:get_weather", '
                        '"arguments": {"city": "北京"}}]}\n'
                    )
                # Inject skill authoring guide when skill_writer is available
                if "skill_writer" in self.skills._skills:
                    tool_desc += (
                        "\n\n# Skill Authoring Guide\n"
                        "If the user asks you to create a new skill / tool / plugin, "
                        "follow this exact workflow:\n"
                        "1. Call skill_writer:create_skill to write the draft code.\n"
                        "2. Call skill_tester:test_skill to validate it.\n"
                        "3. If tests fail, analyze errors and call "
                        "skill_writer:update_skill to fix, then test again.\n"
                        "4. Repeat steps 2-3 until skill_tester:test_skill returns passed=true.\n"
                        "5. Only then call skill_writer:install_skill to activate the skill.\n\n"
                        "Code constraints for new skills:\n"
                        "- Allowed libraries: Python stdlib + httpx + pydantic only.\n"
                        "- Forbidden: os, subprocess, sys, eval, exec, compile, __import__.\n"
                        "- Every tool function must have type hints and a docstring.\n"
                        '- __init__.py must expose `tools = {"name": function, ...}`.'
                    )
                system_parts.append(f"# Tools\n{tool_desc}")

        if include_live2d_tags:
            live2d_prompt = self._build_live2d_tag_prompt()
            if live2d_prompt:
                system_parts.append(live2d_prompt)
            # 注入当前模型状态快照
            state = self._live2d_state
            if state:
                state_lines = ["## 紫羽当前状态"]
                if state.get("pose_description"):
                    state_lines.append(f"- 姿态：{state['pose_description']}")
                if state.get("emotion_description"):
                    state_lines.append(f"- 表情：{state['emotion_description']}")
                if state.get("props_description"):
                    state_lines.append(f"- 装饰：{state['props_description']}")
                system_parts.append("\n".join(state_lines))

        messages: list[Any] = [AIMessage(role="system", content="\n\n".join(system_parts))]

        session = self.sessions.get(session_id)
        if session:
            filtered = self._filter_incomplete_tool_calls(session.messages)
            for msg in filtered:
                messages.append(
                    AIMessage(
                        role=msg.role,
                        content=msg.content,
                        tool_calls=msg.tool_calls,
                        tool_call_id=msg.tool_call_id,
                        reasoning_content=msg.reasoning_content,
                    )
                )

        # Debug: print FULL messages sent to LLM (no truncation)
        self._logger.debug(
            "LLM request",
            session_id=session_id,
            tools_include=include_tools,
            message_count=len(messages),
            messages=[
                {
                    "role": getattr(m, "role", "unknown"),
                    "content": (getattr(m, "content", "") or "")[:200],
                    "tool_calls": getattr(m, "tool_calls", None),
                }
                for m in messages
            ],
        )

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

        # Shared parser used by the streaming guard.  It covers embedded JSON
        # and provider-specific DSML text before we fall back to older logic.
        guarded = ToolCallStreamGuard._try_parse_tool_json(text)
        if guarded is not None:
            return guarded

        # 3. Parse DeepSeek DSML format
        # DeepSeek sometimes outputs its native DSML tool-calling syntax
        # instead of the requested JSON. We extract it as a fallback.
        dsml_parsed = self._parse_dsml_tool_calls(text)
        if dsml_parsed is not None:
            return dsml_parsed

        # 4. Extract a {"tool_calls": [...]} object that appears embedded
        #    inside prose.  Some models prepend/append chitchat around the
        #    JSON instead of outputting ONLY the JSON.  We scan for the
        #    "tool_calls" key and then balance braces from its opening '{'.
        #    This is narrower than the old "any brace anywhere" scan, so
        #    examples embedded in the *middle* of long explanations are
        #    less likely to trigger spurious executions.
        import re

        tc_marker = re.search(r'"tool_calls"\s*:', text)
        if tc_marker:
            start = tc_marker.start()
            while start > 0 and text[start] != "{":
                start -= 1
            if text[start] == "{":
                depth = 1
                for i in range(start + 1, len(text)):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            result = _try_parse(text[start : i + 1])
                            if result is not None:
                                return result
                            break

        return None

    @staticmethod
    def _parse_dsml_tool_calls(text: str) -> list[dict[str, Any]] | None:
        """Parse DeepSeek's DSML tool-calling syntax as a fallback.

        DeepSeek may output:
            <｜DSML｜tool_calls>
            <｜DSML｜invoke name="skill:tool">
            <｜DSML｜parameter name="param" string="true">value</｜DSML｜parameter>
            </｜DSML｜invoke>
            </｜DSML｜tool_calls>
        We extract the calls into our standard JSON shape.
        """
        import re

        if "<｜DSML｜invoke" not in text:
            return None

        invoke_pat = re.compile(
            r'<｜DSML｜invoke\s+name="([^"]+)">(.*?)</｜DSML｜invoke>', re.DOTALL
        )
        param_pat = re.compile(
            r'<｜DSML｜parameter\s+name="([^"]+)"(?:\s+\w+="[^"]*")*>([^<]*)</｜DSML｜parameter>',
            re.DOTALL,
        )

        calls: list[dict[str, Any]] = []
        for inv in invoke_pat.finditer(text):
            name = inv.group(1)
            args: dict[str, Any] = {}
            for pm in param_pat.finditer(inv.group(2)):
                arg_name = pm.group(1)
                arg_val = pm.group(2).strip()
                # Try numeric / bool coercion
                if arg_val.lower() == "true":
                    args[arg_name] = True
                elif arg_val.lower() == "false":
                    args[arg_name] = False
                else:
                    try:
                        if "." in arg_val:
                            args[arg_name] = float(arg_val)
                        else:
                            args[arg_name] = int(arg_val)
                    except ValueError:
                        args[arg_name] = arg_val
            calls.append({"name": name, "arguments": args})

        return calls if calls else None

    def _filter_tools_by_names(
        self, tool_names: list[str]
    ) -> tuple[list[dict[str, str]], list[Any]]:
        """Return only the briefs and full schemas for the requested tool names.

        This shrinks the re-query context in Phase-2 so the model is not
        distracted by unrelated tools.
        """
        all_briefs = self.skills.list_tools_brief()
        name_set = set(tool_names)
        briefs = [b for b in all_briefs if b["name"] in name_set]

        all_tools = self.skills.list_tools() if self.skills else []
        tools = [t for t in all_tools if t.function.get("name", "") in name_set]
        return briefs, tools

    @staticmethod
    def _phase2_tool_names(tool_calls: list[dict[str, Any]]) -> list[str]:
        """Return real tool names that should stay visible in the next round."""
        names: list[str] = []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            if name == "system:get_tool_schema":
                target = args.get("tool_name", "") if isinstance(args, dict) else ""
                if target:
                    names.append(target)
            elif name:
                names.append(name)
        return list(dict.fromkeys(names))

    async def _resolve_tool_calls(
        self,
        session_id: str,
        initial_text: str,
        message_id: str | None = None,
        raw_tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str = "",
    ) -> Message:
        """If the AI response contains tool calls, execute them and get the final reply."""

        ai_provider = self._create_role_ai_provider("tool")
        full_text = initial_text.strip()

        parsed = raw_tool_calls if raw_tool_calls is not None else self._parse_tool_calls(full_text)

        self._logger.debug("Tool resolve enter", initial_text=full_text[:200])
        self._logger.debug("Tool resolve parsed", parsed=parsed)

        loop_idx = -1
        reasoning_text = ""
        max_loops = max(1, min(self.config.max_tool_loops, 10))
        for loop_idx in range(max_loops):
            if not parsed:
                self._logger.debug("Tool resolve no calls, breaking", loop_idx=loop_idx)
                break

            self._logger.debug("Tool resolve executing", loop_idx=loop_idx, count=len(parsed))

            # Notify frontend that the assistant is thinking / executing tools
            tool_names = [tc.get("name", "tool") for tc in parsed]
            await self._broadcast(
                {
                    "type": "event",
                    "method": "chat.thinking",
                    "payload": {
                        "session_id": session_id,
                        "message_id": message_id,
                        "status": "executing_tools",
                        "tool_names": tool_names,
                    },
                }
            )

            # Ensure every tool call has a unique id BEFORE creating the
            # assistant message, so the id is persisted together with it.
            # (session persistence may deep-copy messages, so mutating
            # parsed after add_message won't affect the saved object.)
            for tc in parsed:
                if not tc.get("id"):
                    tc["id"] = f"call_{uuid.uuid4().hex[:8]}"

            # Serialize tool_calls into content so the model can see its own
            # previous tool calls in the conversation history.
            tool_calls_json = json.dumps({"tool_calls": parsed}, ensure_ascii=False)
            assistant_msg = Message(
                role="assistant",
                content=tool_calls_json,
                tool_calls=parsed,
                reasoning_content=reasoning_content,
            )
            await self.sessions.add_message(session_id, assistant_msg)

            for tc in parsed:
                name = tc.get("name", "")
                args = tc.get("arguments", {})
                call_id = tc["id"]
                self._logger.debug("Tool exec", name=name, args=args)
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
                    self._logger.debug(
                        "Tool result",
                        name=name,
                        result=result[:300]
                        if isinstance(result, str) and len(result) > 300
                        else result,
                    )
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
                    self._logger.error("Tool error", name=name, result=result)
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

            self._logger.debug("Re-querying AI", loop_idx=loop_idx)

            # Phase-2 optimisation: only include the tools that were actually
            # invoked in the previous round.  This keeps the context small and
            # prevents the model from being distracted by unrelated skills.
            active_tool_names = self._phase2_tool_names(parsed)
            tool_briefs, tools = self._filter_tools_by_names(active_tool_names)
            tool_schemas = [t.function for t in tools]
            ai_messages = self._build_ai_messages(
                session_id,
                include_tools=True,
                include_live2d_tags=True,
                tool_briefs=tool_briefs,
                tool_schemas=tool_schemas,
            )
            full_text = ""
            reasoning_text = ""
            next_raw_tool_calls: list[dict[str, Any]] | None = None
            async for chunk in ai_provider.chat(ai_messages, tools=None):
                full_text += chunk.delta
                if getattr(chunk, "reasoning_content", None):
                    reasoning_text += chunk.reasoning_content
                if getattr(chunk, "tool_calls", None):
                    next_raw_tool_calls = chunk.tool_calls

            self._logger.info("AI round reply", loop_idx=loop_idx, reply=full_text[:300])
            self._logger.info(
                "Raw tool calls", loop_idx=loop_idx, raw_tool_calls=next_raw_tool_calls
            )

            parsed = (
                next_raw_tool_calls
                if next_raw_tool_calls is not None
                else self._parse_tool_calls(full_text)
            )
            self._logger.info("Next parsed", loop_idx=loop_idx, parsed=parsed)

        self._logger.info("Tool resolve done", final_content=full_text[:300], loop_count=loop_idx)

        final_text = full_text.strip()
        if not final_text:
            self._logger.warning("Tool resolve returned empty content, using fallback")
            final_text = "（已执行工具，但未获得回复）"
        return Message(
            id=message_id or str(uuid.uuid4()),
            role="assistant",
            content=final_text,
            reasoning_content=reasoning_text,
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
        from aipet.gateway.skills.schema import build_tool_schema

        schema = build_tool_schema(
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
        self._logger.info(
            "Client connected", client_id=client_id, remote_address=str(websocket.remote_address)
        )

        try:
            async for message in websocket:
                await self._process_message(client_id, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            async with self._lock:
                self.clients.pop(client_id, None)
            await self.store.update(
                lambda s: s.model_copy(
                    update={"clients": {k: v for k, v in s.clients.items() if k != client_id}}
                )
            )
            self._logger.info("Client disconnected", client_id=client_id)

    async def _process_message(self, client_id: str, raw_message: str | bytes) -> None:
        """Parse and dispatch incoming client messages."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            await self._send_error(client_id, "Invalid JSON")
            return

        req_id = data.get("id")
        req_token = _current_request_id.set(req_id if isinstance(req_id, str) else None)
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
            "live2d.state_report": self._handle_live2d_state_report,
            "tts.stop": self._handle_tts_stop,
            "system.get_settings": self._handle_system_get_settings,
            "system.update_settings": self._handle_system_update_settings,
            "system.shutdown": self._handle_shutdown,
        }

        handler = handlers.get(method)
        if handler is None:
            await self._send_error(client_id, f"Unknown method: {method}")
            return

        try:
            await handler(client_id, payload)
        except Exception as exc:
            import traceback

            self._logger.error(
                "Handler failed", method=method, exc=str(exc), traceback=traceback.format_exc()
            )
            await self._send_error(client_id, f"Internal error: {exc}")
        finally:
            _current_request_id.reset(req_token)

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
        include_tools, tool_briefs, tool_schemas = await self._plan_tool_context(content)
        ai_messages = self._build_ai_messages(
            session.id,
            include_tools=include_tools,
            include_live2d_tags=True,
            tool_briefs=tool_briefs,
            tool_schemas=tool_schemas,
        )
        tools = None
        full_text = ""
        raw_tool_calls: list[dict[str, Any]] | None = None
        async for chunk in ai_provider.chat(ai_messages, tools=tools):
            full_text += chunk.delta
            if getattr(chunk, "tool_calls", None):
                raw_tool_calls = chunk.tool_calls

        assistant_msg = await self._resolve_tool_calls(
            session.id, full_text, raw_tool_calls=raw_tool_calls
        )
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

    async def _plan_tool_context(
        self, content: str
    ) -> tuple[bool, list[dict[str, str]] | None, list[dict[str, Any]] | None]:
        """Decide whether the next model call should include tool instructions."""
        include_tools = True
        tool_briefs: list[dict[str, str]] | None = None
        tool_schemas: list[dict[str, Any]] | None = None

        if self.config.tool_calling_strategy != "phase1_decision":
            return include_tools, tool_briefs, tool_schemas

        candidate_briefs = self._select_candidate_tools(content)
        if not candidate_briefs:
            return False, None, None

        ai_provider = self._create_role_ai_provider("intent")
        decider = ToolDecider(ai_provider, max_tokens=self.config.phase1_max_tokens)
        try:
            decision = await decider.decide(content, candidate_briefs)
        except Exception:
            self._logger.exception("Phase-1 decision failed; falling back to tool prompts")
            return True, candidate_briefs, None

        threshold = self.config.phase1_direct_confidence_threshold
        if decision.action == "direct" and decision.confidence >= threshold:
            self._logger.info("Phase-1 decision: direct reply", confidence=decision.confidence)
            return False, None, None

        if decision.action == "direct":
            self._logger.info(
                "Phase-1 direct decision below threshold; keeping tool prompts",
                confidence=decision.confidence,
            )
            return True, candidate_briefs, None

        if decision.selected_tools:
            selected_names = {t.name for t in decision.selected_tools}
            selected_briefs = [b for b in candidate_briefs if b["name"] in selected_names]
            if self.config.tool_context_mode == "direct_schema":
                _, tools = self._filter_tools_by_names([b["name"] for b in selected_briefs])
                tool_schemas = [t.function for t in tools]
            self._logger.info(
                "Phase-1 decision: use tools",
                selected=[b["name"] for b in selected_briefs],
                confidence=decision.confidence,
            )
            return True, selected_briefs, tool_schemas

        self._logger.info("Phase-1 tool decision had no valid tools; keeping candidates")
        return True, candidate_briefs, None

    def _select_candidate_tools(self, user_content: str) -> list[dict[str, str]]:
        """Return candidate tool briefs for Phase-1 decision.

        Uses a lightweight keyword-overlap heuristic:
        1. Extract English words from the user query.
        2. Score each skill by overlap with its name (high weight) and brief
           (medium weight).
        3. Return only the top-scoring matches.

        If the query contains no English words (e.g. pure Chinese) or nothing
        matches, fall back to returning all briefs (truncated to *max_k*).
        """
        briefs = self.skills.list_tools_brief()
        max_k = self.config.phase1_max_candidate_tools
        lowered_content = user_content.lower()
        intent_keywords: dict[str, tuple[str, ...]] = {
            "weather": ("weather", "\u5929\u6c14", "\u6c14\u6e29", "\u4e0b\u96e8", "\u7a7f\u8863"),
            "time": ("time", "\u65f6\u95f4", "\u51e0\u70b9", "\u65e5\u671f"),
            "file": ("file", "\u6587\u4ef6", "\u8bfb\u53d6", "\u5199\u5165", "\u76ee\u5f55"),
            "scheduler": ("schedule", "remind", "\u63d0\u9192", "\u5b9a\u65f6", "\u8ba1\u5212"),
            "calculator": ("calculate", "calculator", "\u8ba1\u7b97", "\u7b97\u4e00\u4e0b"),
            "random": ("random", "dice", "\u968f\u673a", "\u9ab0\u5b50", "\u62bd\u7b7e"),
            "canvas": ("canvas", "card", "\u5361\u7247", "\u6c14\u6ce1", "\u5c55\u793a"),
            "memory": ("memory", "\u8bb0\u4f4f", "\u8bb0\u5fc6", "\u5fd8\u6389"),
            "shell": ("shell", "command", "\u547d\u4ee4", "\u7ec8\u7aef"),
            "skill_writer": ("skill", "plugin", "\u6280\u80fd", "\u5de5\u5177", "\u63d2\u4ef6"),
            "skill_tester": ("test", "\u6d4b\u8bd5", "\u9a8c\u8bc1"),
        }

        # Extract English words from the user query.
        query_words = set(re.findall(r"[a-zA-Z]{2,}", user_content.lower()))
        if not query_words and any(
            any(keyword in lowered_content for keyword in keywords)
            for keywords in intent_keywords.values()
        ):
            query_words = {"__tool_intent__"}

        if not query_words:
            # Pure Chinese / no recognizable keywords → conservative fallback.
            if max_k and len(briefs) > max_k:
                return briefs[:max_k]
            return briefs

        scored: list[tuple[int, dict[str, str]]] = []
        for b in briefs:
            score = 0
            name = b["name"].lower()
            brief_text = b["brief"].lower()
            skill_id = name.split(":", 1)[0]
            for target_skill, keywords in intent_keywords.items():
                if target_skill == skill_id and any(k in lowered_content for k in keywords):
                    score += 100

            # Name overlap (high weight) – e.g. "weather" in "weather:get_weather"
            name_words = set(re.findall(r"[a-zA-Z]{2,}", name))
            overlap = len(query_words & name_words)
            score += overlap * 10

            # Brief overlap (medium weight)
            brief_words = set(re.findall(r"[a-zA-Z]{2,}", brief_text))
            overlap = len(query_words & brief_words)
            score += overlap * 3

            scored.append((score, b))

        scored.sort(reverse=True, key=lambda x: x[0])

        # Only return skills that actually matched.
        matched = [b for s, b in scored if s > 0]
        if matched:
            return matched[:max_k]

        # Nothing matched → fallback to all briefs.
        if max_k and len(briefs) > max_k:
            return briefs[:max_k]
        return briefs

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
        await self._maybe_compact_session(session.id)
        augmented_content = self._augment_content_with_attachments(content, attachments)
        user_msg = Message(role="user", content=augmented_content, attachments=attachments)
        await self.sessions.add_message(session.id, user_msg)
        await self._maybe_auto_rename_session(session, content)

        self._logger.debug("Chat stream user message", content=content)

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
        include_tools, tool_briefs, tool_schemas = await self._plan_tool_context(content)

        ai_messages = self._build_ai_messages(
            session.id,
            include_tools=include_tools,
            include_live2d_tags=True,
            tool_briefs=tool_briefs,
            tool_schemas=tool_schemas,
        )
        tools = None
        full_text = ""
        reasoning_text = ""
        raw_tool_calls: list[dict[str, Any]] | None = None
        guard = (
            ToolCallStreamGuard(max_buffer_chars=65536, hold_initial_text=include_tools)
            if self.config.enable_streaming_guard and include_tools
            else None
        )
        try:
            async for chunk in ai_provider.chat(ai_messages, tools=tools):
                full_text += chunk.delta
                if getattr(chunk, "reasoning_content", None):
                    reasoning_text += chunk.reasoning_content
                if chunk.delta:
                    self._logger.debug("Chat stream chunk", delta=chunk.delta)
                    if guard is not None:
                        for ev in guard.feed(chunk.delta):
                            await self._broadcast(
                                {
                                    "type": "event",
                                    "method": ev.method,
                                    "payload": {
                                        "session_id": session.id,
                                        "message_id": msg_id,
                                        **ev.payload,
                                    },
                                }
                            )
                    else:
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
                    self._logger.debug("Native tool_calls", raw_tool_calls=raw_tool_calls)
            if guard is not None:
                for ev in guard.flush():
                    await self._broadcast(
                        {
                            "type": "event",
                            "method": ev.method,
                            "payload": {
                                "session_id": session.id,
                                "message_id": msg_id,
                                **ev.payload,
                            },
                        }
                    )
        except Exception as exc:
            self._logger.error(
                "AI chat failed",
                exc=str(exc),
                ai_messages=[
                    {"role": m.role, "content": (m.content or "")[:100]} for m in ai_messages
                ],
            )
            raise

        has_tool_calls = (
            raw_tool_calls is not None or self._parse_tool_calls(full_text.strip()) is not None
        )
        await self._broadcast(
            {
                "type": "event",
                "method": "chat.stream.end",
                "payload": {
                    "session_id": session.id,
                    "message_id": msg_id,
                    "finish_reason": "stop",
                    "has_tool_calls": has_tool_calls,
                },
            }
        )

        final_msg = await self._resolve_tool_calls(
            session.id,
            full_text.strip(),
            message_id=msg_id,
            raw_tool_calls=raw_tool_calls,
            reasoning_content=reasoning_text,
        )
        cleaned_text, live2d_tags = self._parse_live2d_tags(final_msg.content)
        if cleaned_text != final_msg.content:
            final_msg.content = cleaned_text
        await self._emit_live2d_tags(live2d_tags)
        self._logger.debug("Final message", content=final_msg.content[:300])
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
        requires_api_key = bool(entry and entry.type in {"openai", "gemini", "anthropic"})
        is_configured = bool(entry and (not requires_api_key or entry.api_key))
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "provider.get_current",
                "payload": {
                    "provider": entry.model_dump(mode="json") if entry else None,
                    "current_provider_id": self.provider_manager.current_provider_id,
                    "current_model": self.provider_manager.current_model,
                    "is_configured": is_configured,
                    "requires_api_key": requires_api_key,
                    "config_path": str(self.provider_manager._config_path()),
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

    async def _handle_live2d_state_report(self, client_id: str, payload: dict[str, Any]) -> None:
        """Receive state snapshot from frontend and cache it."""
        self._live2d_state = payload
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "live2d.state_report",
                "payload": {"status": "ok"},
            },
        )

    async def _handle_live2d_set_model(self, client_id: str, payload: dict[str, Any]) -> None:
        """Update the current Live2D model so prompts can inject correct tags."""
        model_name = payload.get("model_name", "")
        await self.store.patch(current_live2d_model=model_name)
        self._logger.info("Live2D model set", model_name=model_name)
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "live2d.set_model",
                "payload": {"success": True, "model_name": model_name},
            },
        )

    async def _handle_system_get_settings(self, client_id: str, payload: dict[str, Any]) -> None:
        """Return user-facing runtime toggles."""
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "system.get_settings",
                "payload": self._runtime_settings_payload(),
            },
        )

    async def _handle_system_update_settings(self, client_id: str, payload: dict[str, Any]) -> None:
        """Update user-facing runtime toggles without restarting Gateway."""
        if "tts_auto_play" in payload:
            self.config.tts_auto_play = bool(payload["tts_auto_play"])
        if "proactive_enabled" in payload:
            self.config.proactive_enabled = bool(payload["proactive_enabled"])
        if "proactive_tts" in payload:
            self.config.proactive_tts = bool(payload["proactive_tts"])
        if "tool_calling_strategy" in payload:
            strategy = payload["tool_calling_strategy"]
            if strategy in {"legacy", "phase1_decision"}:
                self.config.tool_calling_strategy = strategy
        if "tool_context_mode" in payload:
            mode = payload["tool_context_mode"]
            if mode in {"brief_schema", "direct_schema"}:
                self.config.tool_context_mode = mode
        if "phase1_max_candidate_tools" in payload:
            self.config.phase1_max_candidate_tools = max(
                1, min(int(payload["phase1_max_candidate_tools"]), 50)
            )
        if "phase1_direct_confidence_threshold" in payload:
            self.config.phase1_direct_confidence_threshold = max(
                0.0, min(float(payload["phase1_direct_confidence_threshold"]), 1.0)
            )
        if "max_tool_loops" in payload:
            self.config.max_tool_loops = max(1, min(int(payload["max_tool_loops"]), 10))
        if "enable_streaming_guard" in payload:
            self.config.enable_streaming_guard = bool(payload["enable_streaming_guard"])
        if "tool_debug_events" in payload:
            self.config.tool_debug_events = bool(payload["tool_debug_events"])
        for role in ("intent", "tool", "summary", "proactive"):
            provider_key = f"{role}_provider_id"
            model_key = f"{role}_model"
            if provider_key in payload:
                value = payload[provider_key]
                setattr(self.config, provider_key, value or None)
            if model_key in payload:
                value = payload[model_key]
                setattr(self.config, model_key, value or None)
        self._save_ai_tool_settings()

        settings = self._runtime_settings_payload()
        await self._broadcast(
            {
                "type": "event",
                "method": "system.settings.updated",
                "payload": settings,
            }
        )
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "system.update_settings",
                "payload": {"success": True, **settings},
            },
        )

    def _runtime_settings_payload(self) -> dict[str, Any]:
        return {key: getattr(self.config, key) for key in self._runtime_settings_payload_keys()}

    async def _handle_tts_stop(self, client_id: str, payload: dict[str, Any]) -> None:
        """Stop current TTS playback and clear the queue."""
        self.audio_player.skip_current()
        self.audio_player.clear_queue()
        await self._send(
            client_id,
            {
                "type": "response",
                "method": "tts.stop",
                "payload": {"success": True},
            },
        )
        await self._broadcast(
            {
                "type": "event",
                "method": "tts.end",
                "payload": {"session_id": "main", "reason": "stopped"},
            }
        )

    async def _handle_shutdown(self, client_id: str, payload: dict[str, Any]) -> None:
        """Graceful shutdown request."""
        await self._broadcast({"type": "event", "method": "system.shutdown", "payload": {}})
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
        req_id = _current_request_id.get()
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
        self._logger.info("Starting server", bind=bind, port=port)
        async with websockets.serve(self.handle_client, bind, port):
            await asyncio.Future()  # run forever


class ProactiveChatService:
    """Background service that periodically triggers proactive AI messages."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self._logger = gateway._logger
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
        self._logger.info(
            "ProactiveChat loop started", enabled=self.gateway.config.proactive_enabled
        )
        while not self._stopped.is_set():
            if not self.gateway.config.proactive_enabled:
                if not getattr(self, "_disabled_logged", False):
                    self._logger.debug("Proactive disabled, waiting...")
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
            self._logger.debug("ProactiveChat next trigger", interval=interval)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                self._logger.info("ProactiveChat stop signal received")
                return
            except TimeoutError:
                pass

            self._logger.info("Triggering proactive message")
            await self._trigger()

    async def _trigger(self) -> None:
        """Generate and broadcast a proactive message."""
        self._logger.debug("ProactiveChat _trigger() started")
        if not self.gateway.clients:
            self._logger.debug("No clients connected, skipping proactive")
            return

        # Skip if user was recently active (within 30 seconds)
        if time.time() - self.gateway._last_user_activity < 30:
            self._logger.debug("User is active, skipping proactive message")
            return

        try:
            session_id = self.gateway.store.state.current_session_id or "main"
            session = await self.gateway._ensure_session(session_id)
            self._logger.debug("Session ensured", session_id=session.id)
            from aipet.gateway.providers.ai import Message as AIMessage
            from aipet.gateway.providers.ai_echo import EchoProvider
            from aipet.gateway.providers.factory import create_ai_provider

            ai_provider = self.gateway._create_role_ai_provider("proactive")
            name = ai_provider.name
            model_id = ai_provider.model_id
            self._logger.debug("Provider created", name=name, model_id=model_id)
            # Fallback: if ProviderManager yields Echo but gateway.toml has a real key, use it
            if isinstance(ai_provider, EchoProvider) and self.gateway.config.ai_api_key:
                self._logger.debug(
                    "ProviderManager returned Echo, falling back to GatewayConfig provider"
                )
                ai_provider = create_ai_provider(self.gateway.config)
                name = ai_provider.name
                model_id = ai_provider.model_id
                self._logger.debug("Fallback provider", name=name, model_id=model_id)

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
            recent = [
                m
                for m in all_messages[1:]
                if m.role in ("user", "assistant") and not getattr(m, "tool_calls", None)
            ]
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
            self._logger.debug("Calling AI", message_count=len(messages))
            full_text = ""
            chunk_count = 0
            async for chunk in ai_provider.chat(messages):
                full_text += chunk.delta
                chunk_count += 1
            self._logger.debug(
                "AI returned chunks", chunk_count=chunk_count, text_length=len(full_text)
            )

            content = full_text.strip()
            self._logger.debug("Final content", content=content)
            if not content:
                self._logger.debug("Empty content, aborting broadcast")
                return

            # Strip live2d tags from proactive messages too
            cleaned_content, live2d_tags = self.gateway._parse_live2d_tags(content)
            if cleaned_content != content:
                content = cleaned_content

            msg = Message(role="assistant", content=content, source="proactive")
            await self.gateway.sessions.add_message(session.id, msg, update_session=False)
            self._logger.debug("Message saved to session", session_id=session.id)

            # Broadcast any live2d tags from proactive message
            await self.gateway._emit_live2d_tags(live2d_tags)

            # Pick random expression/motion from current model if available
            current_model = self.gateway.store.state.current_live2d_model or ""
            model_tags = self.gateway._live2d_tags.get(current_model, {})
            expressions = model_tags.get("expressions", [])
            motions = model_tags.get("motions", [])
            expression = random.choice(expressions) if expressions else ""
            motion = random.choice(motions) if motions else ""

            self._logger.debug("Broadcasting chat.proactive event")
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
            self._logger.debug("Broadcast complete")

            if self.gateway.config.proactive_tts:
                self._logger.debug("Enqueuing TTS")
                await self.gateway._play_tts(content)
                self._logger.debug("TTS enqueued")
            else:
                self._logger.debug("TTS disabled, skipping")
        except Exception as exc:
            self._logger.exception("ProactiveChat error")
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
    from aipet.utils.log import configure_logging, setup_exception_logging

    config = GatewayConfig()
    configure_logging(config.gateway_log_level, log_file_name="gateway.log")
    setup_exception_logging("gateway")
    logger = structlog.get_logger("gateway.server")

    async def _main() -> int:
        # Install asyncio exception handler now that the loop is running
        loop = asyncio.get_running_loop()

        def _gateway_asyncio_exc_handler(
            _loop: asyncio.AbstractEventLoop, context: dict[str, object]
        ) -> None:
            message = context.get("message", "Unknown asyncio error")
            exception = context.get("exception")
            if exception is not None:
                logger.error(
                    "Asyncio exception: %s | exc=%s", message, exception, exc_info=exception
                )
            else:
                logger.error("Asyncio error: %s | context=%s", message, context)
            _loop.default_exception_handler(context)

        loop.set_exception_handler(_gateway_asyncio_exc_handler)

        gateway = Gateway()
        await gateway.init()
        try:
            await gateway.run()
        except KeyboardInterrupt:
            logger.info("Gateway shutting down...")
        finally:
            await gateway.proactive.stop()
        await gateway.scheduler.stop()
        return 0

    return asyncio.run(_main())
