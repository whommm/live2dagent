# AIPet v2.0 — OpenClaw 优秀实践采纳设计文档

> 本文档详细描述从 OpenClaw 参考项目中借鉴的 10 项改进的接口定义、改动点和实现方案。
> 待确认后按此文档编码实现。

---

## 目录

1. [Provider Stream Wrapper 组合模式](#1-provider-stream-wrapper-组合模式)
2. [认证档案轮换 + 模型故障转移](#2-认证档案轮换--模型故障转移)
3. [工具结果截断](#3-工具结果截断)
4. [工具工厂模式](#4-工具工厂模式)
5. [Prompt 缓存稳定性](#5-prompt-缓存稳定性)
6. [延迟加载与运行时隔离](#6-延迟加载与运行时隔离)
7. [结果类型与闭路错误码](#7-结果类型与闭路错误码)
8. [网关 RPC 方法注册表](#8-网关-rpc-方法注册表)
9. [会话元数据快照](#9-会话元数据快照)
10. [子代理协作](#10-子代理协作)

---

## 1. Provider Stream Wrapper 组合模式

### 背景

当前每个 Provider（Gemini、OpenAI、Anthropic、Ollama）独立实现完整的 HTTP/流式逻辑，通用行为（重试、超时、思考标签过滤、速率限制处理）散落在各文件中。新增一个 Provider 需要复制粘贴大量样板代码。

OpenClaw 采用**函数式包装器链**：`composeProviderStreamWrappers(baseStreamFn, ...wrappers)`，每个包装器叠加特定行为。

### 设计目标

- 新增 Provider 只需实现"原始流"，通用行为自动叠加
- 通用包装器（重试、超时、过滤）一处实现，全局生效
- 运行时可根据 Provider 类型动态选择包装器组合

### 接口定义

```python
# src/aipet/gateway/providers/stream_wrappers.py

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Protocol

from aipet.gateway.providers.ai import Chunk, Message, Tool

# 核心类型别名
StreamFn = Callable[
    [list[Message], list[Tool] | None, float, int | None],
    AsyncIterator[Chunk],
]

class ProviderStreamWrapper(Protocol):
    """A wrapper that takes a base stream and returns a new stream."""

    def __call__(self, stream_fn: StreamFn) -> StreamFn:
        ...


def compose_stream_wrappers(
    base_stream: StreamFn,
    *wrappers: ProviderStreamWrapper | None,
) -> StreamFn:
    """Compose wrappers from right to left: last wrapper is outermost."""
    result = base_stream
    for wrapper in wrappers:
        if wrapper is not None:
            result = wrapper(result)
    return result
```

### 标准包装器实现

```python
# src/aipet/gateway/providers/wrappers.py

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog

from aipet.gateway.providers.ai import Chunk, Message, Tool
from aipet.gateway.providers.stream_wrappers import StreamFn

_logger = structlog.get_logger("gateway.providers.wrappers")


def retry_wrapper(
    max_retries: int = 3,
    backoff_base: float = 1.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> StreamFn:
    """Retry on transient failures with exponential backoff."""

    def _wrapper(next_fn: StreamFn) -> StreamFn:
        async def _stream(
            messages: list[Message],
            tools: list[Tool] | None,
            temperature: float,
            max_tokens: int | None,
        ) -> AsyncIterator[Chunk]:
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    async for chunk in next_fn(messages, tools, temperature, max_tokens):
                        yield chunk
                    return
                except retryable_exceptions as exc:
                    last_exc = exc
                    _logger.warning(
                        "Provider stream error, retrying",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        exc=str(exc),
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(backoff_base * (2 ** attempt))
            if last_exc:
                raise last_exc

        return _stream

    return _wrapper


def timeout_wrapper(total_timeout: float = 120.0) -> StreamFn:
    """Enforce a total timeout on the entire streaming operation."""

    def _wrapper(next_fn: StreamFn) -> StreamFn:
        async def _stream(
            messages: list[Message],
            tools: list[Tool] | None,
            temperature: float,
            max_tokens: int | None,
        ) -> AsyncIterator[Chunk]:
            iterator = next_fn(messages, tools, temperature, max_tokens).__aiter__()
            start = asyncio.get_event_loop().time()

            while True:
                elapsed = asyncio.get_event_loop().time() - start
                remaining = total_timeout - elapsed
                if remaining <= 0:
                    raise TimeoutError(f"Provider stream timed out after {total_timeout}s")

                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                    yield chunk
                except StopAsyncIteration:
                    break

        return _stream

    return _wrapper


def thinking_filter_wrapper() -> StreamFn:
    """Strip <thinking>...</thinking> tags from model output (e.g. DeepSeek R1)."""

    def _wrapper(next_fn: StreamFn) -> StreamFn:
        async def _stream(
            messages: list[Message],
            tools: list[Tool] | None,
            temperature: float,
            max_tokens: int | None,
        ) -> AsyncIterator[Chunk]:
            in_thinking = False
            buffer = ""
            async for chunk in next_fn(messages, tools, temperature, max_tokens):
                text = chunk.delta
                while text:
                    if not in_thinking:
                        idx = text.find("<thinking>")
                        if idx == -1:
                            if text.endswith("<") and "<thinking>".startswith(text[-1:]):
                                buffer = text[-1:]
                                text = text[:-1]
                                if text:
                                    yield chunk.model_copy(update={"delta": text})
                            else:
                                yield chunk.model_copy(update={"delta": text})
                            break
                        else:
                            if idx > 0:
                                yield chunk.model_copy(update={"delta": text[:idx]})
                            in_thinking = True
                            text = text[idx + len("<thinking>"):]
                    else:
                        idx = text.find("</thinking>")
                        if idx == -1:
                            break  # discard everything
                        in_thinking = False
                        text = text[idx + len("</thinking>"):]
                if chunk.finish_reason:
                    yield chunk

        return _stream

    return _wrapper


def rate_limit_wrapper(
    requests_per_minute: int = 60,
) -> StreamFn:
    """Simple token-bucket rate limiter across all calls to this provider."""

    import time

    def _wrapper(next_fn: StreamFn) -> StreamFn:
        tokens = requests_per_minute
        last_update = time.monotonic()
        lock = asyncio.Lock()

        async def _acquire() -> None:
            nonlocal tokens, last_update
            async with lock:
                now = time.monotonic()
                tokens = min(
                    requests_per_minute,
                    tokens + (now - last_update) * (requests_per_minute / 60.0),
                )
                last_update = now
                if tokens < 1:
                    wait = (1 - tokens) * (60.0 / requests_per_minute)
                    _logger.info("Rate limit: waiting", wait_seconds=wait)
                    await asyncio.sleep(wait)
                    tokens = 0
                else:
                    tokens -= 1

        async def _stream(
            messages: list[Message],
            tools: list[Tool] | None,
            temperature: float,
            max_tokens: int | None,
        ) -> AsyncIterator[Chunk]:
            await _acquire()
            async for chunk in next_fn(messages, tools, temperature, max_tokens):
                yield chunk

        return _stream

    return _wrapper
```

### Provider 适配

```python
# src/aipet/gateway/providers/ai_openai.py（简化后）

class OpenAIProvider:
    def __init__(self, api_key: str, model_id: str = "gpt-4o", base_url: str = "..."):
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)
        # 内部原始流，不含任何包装逻辑
        self._raw_stream = self._build_raw_stream()
        # 组合包装器
        from aipet.gateway.providers.wrappers import (
            compose_stream_wrappers,
            retry_wrapper,
            timeout_wrapper,
            thinking_filter_wrapper,
        )
        self.chat = compose_stream_wrappers(
            self._raw_stream,
            retry_wrapper(max_retries=3, retryable_exceptions=(httpx.HTTPStatusError, httpx.NetworkError)),
            timeout_wrapper(total_timeout=180.0),
            thinking_filter_wrapper(),
        )

    def _build_raw_stream(self) -> StreamFn:
        async def _stream(messages, tools, temperature, max_tokens):
            # ... 原始 HTTP 流式逻辑，不做重试/超时 ...
            pass
        return _stream
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `providers/stream_wrappers.py` | 核心协议和 `compose_stream_wrappers` | StreamFn + ProviderStreamWrapper 类型定义 |
| **新增** `providers/wrappers.py` | 标准包装器实现 | retry、timeout、thinking_filter、rate_limit |
| 修改 `providers/ai_openai.py` | 拆分原始流和包装流 | 移除内部重试逻辑，改为包装器组合 |
| 修改 `providers/ai_gemini.py` | 同上 | 移除内部异常处理，改为包装器组合 |
| 修改 `providers/ai_anthropic.py` | 同上 | 同上 |
| 修改 `providers/ai_ollama.py` | 同上 | 同上 |
| 修改 `providers/manager.py` | 根据 provider 类型选择包装器 | 如 Ollama 不需要 rate_limit，OpenAI 需要 retry |

### 兼容性

- 现有 `AIProvider.chat()` Protocol 签名不变
- 每个 Provider 内部重构，外部调用方无感知

---

## 2. 认证档案轮换 + 模型故障转移

### 背景

当前 `ProviderEntry` 只有一个 `api_key`，单点失效即全部中断。OpenClaw 支持每个 Provider 配置多个 `AuthProfile`，带使用统计、冷却期、失败标记，自动轮换到下一个可用档案。

### 设计目标

- 一个 Provider 可配置多个 API Key，自动轮换
- 当主模型失败（429、超时、上下文溢出）时，按回退链切换模型
- 档案状态持久化（冷却期、失败计数）

### 数据模型

```python
# src/aipet/gateway/providers/auth.py

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AuthProfileStatus(str, Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    EXHAUSTED = "exhausted"


class AuthProfile(BaseModel):
    """A single authentication credential for a provider."""

    id: str
    api_key: str
    base_url: str | None = None  # override provider-level base_url
    status: AuthProfileStatus = AuthProfileStatus.ACTIVE
    fail_count: int = 0
    last_fail_at: datetime | None = None
    cooldown_until: datetime | None = None
    usage_count: int = 0
    max_fail_before_cooldown: int = 3
    cooldown_seconds: int = 60

    def record_success(self) -> None:
        self.fail_count = 0
        self.usage_count += 1

    def record_failure(self) -> None:
        self.fail_count += 1
        self.last_fail_at = datetime.utcnow()
        if self.fail_count >= self.max_fail_before_cooldown:
            self.status = AuthProfileStatus.COOLDOWN
            self.cooldown_until = datetime.utcnow() + timedelta(seconds=self.cooldown_seconds)

    def is_available(self) -> bool:
        if self.status == AuthProfileStatus.EXHAUSTED:
            return False
        if self.status == AuthProfileStatus.COOLDOWN and self.cooldown_until:
            if datetime.utcnow() >= self.cooldown_until:
                self.status = AuthProfileStatus.ACTIVE
                self.fail_count = 0
                self.cooldown_until = None
                return True
            return False
        return True


class FailoverReason(str, Enum):
    """Closed union of failover reasons."""

    RATE_LIMIT = "rate_limit"          # 429
    TIMEOUT = "timeout"                # 网络/读取超时
    AUTH_ERROR = "auth_error"          # 401/403
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文长度超限
    SERVER_ERROR = "server_error"      # 5xx
    UNKNOWN = "unknown"


class FailoverConfig(BaseModel):
    """Per-provider failover chain."""

    enabled: bool = True
    fallback_models: list[str] = Field(default_factory=list)  # e.g. ["gpt-4o", "gpt-4o-mini"]
    fallback_providers: list[str] = Field(default_factory=list)  # cross-provider fallback IDs
    max_failover_depth: int = 3


class AuthProfileManager(BaseModel):
    """Manages auth profiles for a single provider."""

    profiles: list[AuthProfile] = Field(default_factory=list)
    current_index: int = 0
    failover: FailoverConfig = Field(default_factory=FailoverConfig)

    def get_next_available(self) -> AuthProfile | None:
        """Round-robin select next available profile."""
        checked = 0
        while checked < len(self.profiles):
            profile = self.profiles[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.profiles)
            if profile.is_available():
                return profile
            checked += 1
        return None

    def get_fallback_chain(self) -> list[tuple[AuthProfile | None, str]]:
        """Return ordered list of (profile, model) to try."""
        chain: list[tuple[AuthProfile | None, str]] = []
        seen = set()

        # 1. Try all profiles with primary model
        for p in self.profiles:
            key = (p.id, "primary")
            if key not in seen:
                seen.add(key)
                chain.append((p, "primary"))

        # 2. Try fallback models with first available profile
        if self.failover.enabled:
            first = self.profiles[0] if self.profiles else None
            for m in self.failover.fallback_models:
                key = (first.id if first else "none", m)
                if key not in seen:
                    seen.add(key)
                    chain.append((first, m))

        return chain
```

### ProviderEntry 扩展

```python
# src/aipet/gateway/providers/manager.py

class ProviderEntry(BaseModel):
    id: str
    name: str
    type: str
    api_key: str | None = None          # backward compat: creates a single default profile
    auth_profiles: list[AuthProfile] = Field(default_factory=list)
    base_url: str | None = None
    models: list[str] = Field(default_factory=list)
    is_custom: bool = False
    failover: FailoverConfig = Field(default_factory=FailoverConfig)
```

### 故障转移执行器

```python
# src/aipet/gateway/providers/failover.py

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from aipet.gateway.providers.ai import Chunk, Message, Tool
from aipet.gateway.providers.auth import AuthProfile, AuthProfileManager, FailoverReason

_logger = structlog.get_logger("gateway.providers.failover")


def classify_error(exc: Exception) -> FailoverReason:
    """Classify an exception into a FailoverReason."""
    if isinstance(exc, asyncio.TimeoutError):
        return FailoverReason.TIMEOUT
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return FailoverReason.RATE_LIMIT
        if code in (401, 403):
            return FailoverReason.AUTH_ERROR
        if code >= 500:
            return FailoverReason.SERVER_ERROR
        if code == 400:
            # Heuristic: check body for context length
            body = getattr(exc.response, "text", "") or ""
            if any(kw in body.lower() for kw in ("context length", "too long", "max_tokens")):
                return FailoverReason.CONTEXT_OVERFLOW
    return FailoverReason.UNKNOWN


class FailoverStream:
    """Wraps a provider's chat() to transparently failover across profiles/models."""

    def __init__(
        self,
        auth_manager: AuthProfileManager,
        create_provider_fn: callable,  # (profile: AuthProfile, model: str) -> AIProvider
    ) -> None:
        self._auth_manager = auth_manager
        self._create_provider = create_provider_fn

    async def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        chain = self._auth_manager.get_fallback_chain()
        last_exc: Exception | None = None
        last_reason = FailoverReason.UNKNOWN

        for profile, model in chain:
            if profile is None or not profile.is_available():
                continue
            try:
                provider = self._create_provider(profile, model)
                _logger.info(
                    "Failover attempt",
                    profile_id=profile.id,
                    model=model,
                    provider_type=provider.name,
                )
                async for chunk in provider.chat(messages, tools, temperature, max_tokens):
                    yield chunk
                profile.record_success()
                return
            except Exception as exc:
                last_exc = exc
                last_reason = classify_error(exc)
                profile.record_failure()
                _logger.warning(
                    "Provider attempt failed",
                    profile_id=profile.id,
                    model=model,
                    reason=last_reason,
                    exc=str(exc),
                )
                # Don't failover on auth errors (permanent)
                if last_reason == FailoverReason.AUTH_ERROR:
                    break

        # All attempts exhausted
        if last_exc:
            raise RuntimeError(
                f"All provider attempts failed. Last reason: {last_reason}"
            ) from last_exc
        raise RuntimeError("No available auth profiles")
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `providers/auth.py` | AuthProfile / FailoverConfig / AuthProfileManager | 认证档案和故障转移配置 |
| **新增** `providers/failover.py` | FailoverStream + classify_error | 故障转移执行逻辑 |
| 修改 `providers/manager.py` | ProviderEntry 扩展 auth_profiles + failover | 加载/保存多档案配置 |
| 修改 `providers/manager.py` | create_ai_provider() 签名变更 | 接收 AuthProfile 而非直接从 entry 取 key |
| 修改 `server.py` | _resolve_tool_calls / _handle_chat_stream | 使用新的 FailoverStream.chat() |
| 修改 `config/providers.toml` | 新格式支持 | 向后兼容：单 api_key 自动转单档案 |

### 配置示例 (providers.toml)

```toml
[[providers]]
id = "openai"
name = "OpenAI"
type = "openai"
base_url = "https://api.openai.com/v1"
models = ["gpt-4o", "gpt-4o-mini"]

[[providers.auth_profiles]]
id = "key-1"
api_key = "sk-xxx1"
cooldown_seconds = 60

[[providers.auth_profiles]]
id = "key-2"
api_key = "sk-xxx2"
cooldown_seconds = 60

[providers.failover]
enabled = true
fallback_models = ["gpt-4o-mini"]
max_failover_depth = 3
```

---

## 3. 工具结果截断

### 背景

`shell`、`file` 等技能可能返回巨量文本（如 `cat` 大文件、`find` 递归结果），直接塞入上下文会导致 Token 爆炸甚至模型截断。

### 设计目标

- 工具返回结果超过阈值时自动截断
- 截断策略可配置（保留头尾、仅保留头、智能摘要）
- 截断信息透明告知模型

### 接口定义

```python
# src/aipet/gateway/skills/truncation.py

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class TruncationStrategy(str, Enum):
    HEAD_TAIL = "head_tail"      # 保留头部 + "..." + 尾部
    HEAD_ONLY = "head_only"      # 仅保留头部
    TAIL_ONLY = "tail_only"      # 仅保留尾部
    SMART = "smart"              # 未来：用轻量模型摘要


class TruncationConfig(BaseModel):
    max_chars: int = 8000        # 默认约 2000 tokens（中文字符）
    head_ratio: float = 0.7      # head_tail 模式下头部占比
    strategy: TruncationStrategy = TruncationStrategy.HEAD_TAIL
    add_truncation_notice: bool = True


def truncate_tool_result(
    result: str,
    config: TruncationConfig | None = None,
    tool_name: str = "",
) -> str:
    """Truncate oversized tool results before feeding back to the model."""
    if config is None:
        config = TruncationConfig()

    if len(result) <= config.max_chars:
        return result

    if config.strategy == TruncationStrategy.HEAD_ONLY:
        truncated = result[: config.max_chars]
    elif config.strategy == TruncationStrategy.TAIL_ONLY:
        truncated = result[-config.max_chars :]
    else:  # HEAD_TAIL
        head_len = int(config.max_chars * config.head_ratio)
        tail_len = config.max_chars - head_len
        truncated = result[:head_len] + "\n\n... [truncated] ...\n\n" + result[-tail_len:]

    if config.add_truncation_notice:
        notice = (
            f"[Tool result for '{tool_name}' was truncated: "
            f"{len(result)} chars -> {config.max_chars} chars. "
            f"Use more specific parameters to narrow results.]\n\n"
        )
        truncated = notice + truncated

    return truncated
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `skills/truncation.py` | TruncationConfig + truncate_tool_result | 截断逻辑 |
| 修改 `skills/router.py` | `call()` 返回前截断 | 在结果序列化后截断 |
| 修改 `gateway/config.py` | 新增 `tool_truncation` 配置项 | 用户可配置截断阈值 |

### 集成点（ToolRouter.call）

```python
# 在 router.py 的 call() 方法末尾
result = await self._execute_tool(func, arguments)
if isinstance(result, str) and len(result) > TRUNCATION_THRESHOLD:
    from aipet.gateway.skills.truncation import truncate_tool_result
    result = truncate_tool_result(result, tool_name=full_name)
return result
```

---

## 4. 工具工厂模式

### 背景

当前 Skill Registry 在启动时静态扫描目录，所有工具对所有会话一视同仁。OpenClaw 的 Tool Factory 在每次 Agent Attempt 时根据上下文（安全策略、会话 Key、sender 身份）动态创建工具实例。

### 设计目标

- 同一 skill 在不同安全策略下可表现不同（如用户技能 vs AI 生成技能）
- 工具可感知当前会话上下文
- 为未来的多用户/多租户铺路

### 接口定义

```python
# src/aipet/gateway/skills/factory.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from aipet.gateway.skills.registry import SkillInfo


@dataclass(frozen=True)
class ToolContext:
    """Runtime context passed to tool factories."""

    session_id: str
    skill_id: str
    tool_name: str
    workspace_dir: str | None = None
    sandboxed: bool = True
    caller_role: str = "assistant"  # "assistant" | "scheduler" | "user"


ToolFactory = Callable[[ToolContext], Callable[..., Any] | None]


class ToolFactoryRegistry:
    """Registry of tool factories that can override or wrap static tools."""

    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {}

    def register(self, tool_full_name: str, factory: ToolFactory) -> None:
        self._factories[tool_full_name] = factory

    def create(
        self,
        full_name: str,
        static_func: Callable[..., Any],
        skill: SkillInfo,
        context: ToolContext,
    ) -> Callable[..., Any]:
        """Resolve final tool implementation for this context."""
        factory = self._factories.get(full_name)
        if factory:
            override = factory(context)
            if override is not None:
                return override
        # Default: wrap static func with context injection
        return _ContextToolWrapper(static_func, context)


class _ContextToolWrapper:
    """Wraps a static tool function to inject context as first arg or kwarg."""

    def __init__(self, func: Callable[..., Any], context: ToolContext) -> None:
        self._func = func
        self._context = context

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # If the function signature accepts 'ctx', inject it
        import inspect

        sig = inspect.signature(self._func)
        if "ctx" in sig.parameters:
            kwargs["ctx"] = self._context
        return self._func(*args, **kwargs)
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `skills/factory.py` | ToolContext + ToolFactoryRegistry | 工具工厂基础设施 |
| 修改 `skills/router.py` | call() 接收可选 context | 构建 ToolContext 并传入工厂 |
| 修改 `skills/router.py` | 执行前 resolve 最终函数 | factory_registry.create() |
| 修改 `gateway/server.py` | _resolve_tool_calls | 传入 session_id 构建 ToolContext |

---

## 5. Prompt 缓存稳定性

### 背景

OpenClaw 将 prompt cache stability 视为正确性要素：从 Map/Set/Registry 构建模型请求前，必须**确定性排序**，避免缓存失效；截断时优先修改尾部，保留前缀缓存字节。

### 设计目标

- 工具列表按名称排序后再序列化
- Session messages 在压缩/过滤后保持确定性顺序
- 系统提示词前缀稳定，只修改尾部

### 实现

```python
# src/aipet/gateway/server.py — _build_ai_messages 修改

def _build_ai_messages(self, session_id: str, ...) -> list[Any]:
    # ... existing logic ...

    # 确定性排序：工具列表按名称排序
    if include_tools:
        briefs = self.skills.list_tools_brief()
        briefs = sorted(briefs, key=lambda b: b["name"])  # ← 新增

    # ... rest of logic ...

    # 会话消息已经是时间序，无需额外排序
    # 但过滤后确保不引入非确定性（当前已满足）
```

```python
# src/aipet/gateway/providers/ai_openai.py — _build_payload 修改

def _build_payload(self, messages, tools, temperature, max_tokens):
    # ...
    if tools:
        # 确定性排序
        sorted_tools = sorted(tools, key=lambda t: t.function.get("name", ""))
        payload["tools"] = [t.model_dump() for t in sorted_tools]
    # ...
```

```python
# src/aipet/gateway/session.py — compact_session 修改

async def compact_session(self, session_id: str, summary: str) -> None:
    """Compact from the TAIL (preserve prefix for cache)."""
    session = self._sessions.get(session_id)
    if not session:
        return
    # 保留最近的 10 条在内存（尾部不变）
    # 将更早的消息压缩为摘要（插入到系统提示后）
    # 这样前缀（system prompt）保持稳定，只修改中间部分
    session.memory_summary = summary
    # 持久化到数据库但不删除原始消息（可选择归档）
    await self._repo.save_session(session)
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| `gateway/server.py` | `briefs = sorted(...)` | 工具描述列表确定性排序 |
| `providers/ai_openai.py` | `sorted_tools = sorted(tools, ...)` | tools payload 确定性排序 |
| `providers/ai_gemini.py` | 同上 | gemini_tools 排序 |
| `session.py` | compact 策略注释 + 实现 | 优先压缩中间段，保留前缀 |

---

## 6. 延迟加载与运行时隔离

### 背景

当前 `manager.py` 顶部一次性 import 所有 Provider 模块（`google.genai`、`httpx`、`anthropic` 等），即使只使用 EchoProvider 也会加载全部依赖。OpenClaw 用 `*.runtime.ts` 封装重依赖，需要时才动态 import。

### 设计目标

- Provider 核心模块延迟加载，减少启动时间和内存
- 只有真正调用某个 Provider 时才 import 对应库
- 测试时更容易 mock

### 实现

```python
# src/aipet/gateway/providers/manager.py — 移除顶部 import，改为延迟加载

class ProviderManager:
    def create_ai_provider(self, provider_id=None, model=None):
        # ... entry resolution ...

        if entry.type == "gemini":
            from aipet.gateway.providers.ai_gemini import GeminiProvider
            return GeminiProvider(api_key=profile.api_key, model_id=mid)
        elif entry.type == "openai":
            from aipet.gateway.providers.ai_openai import OpenAIProvider
            return OpenAIProvider(api_key=profile.api_key, model_id=mid, base_url=...)
        # ... 其他同理 ...
```

```python
# src/aipet/gateway/providers/ai_gemini.py — 移除顶部 from google import genai

class GeminiProvider:
    def __init__(self, api_key: str, model_id: str = "..."):
        self._api_key = api_key
        self._model_id = model_id
        self._client = None  # lazy init

    @property
    def _genai_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| `providers/manager.py` | 移除顶部具体 Provider import | 改为 `create_ai_provider` 内局部 import |
| `providers/ai_gemini.py` | `genai.Client` 延迟初始化 | `__init__` 不创建 client，首次 `chat()` 时才创建 |
| `providers/ai_openai.py` | `httpx.AsyncClient` 延迟初始化 | 同上（或保留在 init，因为 httpx 较轻量） |
| `providers/ai_anthropic.py` | anthropic client 延迟初始化 | 同 gemini |

---

## 7. 结果类型与闭路错误码

### 背景

当前代码大量使用自由字符串错误（`error: str`, `reason: str`），运行时拼写错误无法被静态检查捕获。OpenClaw 要求用 closed union 替代。

### 设计目标

- 边界处的错误处理使用类型化结果，而非字符串
- Provider 调用、工具执行、Session 加载等关键点统一错误码
- Python 3.10+ 的 `|` union + `@dataclass` 实现 `Result[T, E]`

### 接口定义

```python
# src/aipet/gateway/result.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


type Result[T, E] = Ok[T] | Err[E]


def is_ok(result: Result[T, E]) -> bool:
    return isinstance(result, Ok)


def is_err(result: Result[T, E]) -> bool:
    return isinstance(result, Err)


def unwrap(result: Result[T, E]) -> T:
    if isinstance(result, Ok):
        return result.value
    raise ValueError(f"Cannot unwrap Err: {result.error}")
```

### 错误码枚举

```python
# src/aipet/gateway/errors.py

from enum import Enum


class ProviderErrorCode(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTH_FAILED = "auth_failed"
    TIMEOUT = "timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_NOT_FOUND = "model_not_found"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


class ToolErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    SANDBOX_VIOLATION = "sandbox_violation"
    INVALID_ARGUMENTS = "invalid_arguments"
    EXECUTION_ERROR = "execution_error"
    RESULT_TRUNCATED = "result_truncated"


@dataclass(frozen=True)
class ProviderError:
    code: ProviderErrorCode
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ToolError:
    code: ToolErrorCode
    message: str
```

### 应用点

```python
# ToolRouter.call() 改造前
async def call(self, full_name: str, arguments: dict, timeout=30.0) -> str:
    ...
    try:
        result = await self._execute(...)
        return result
    except TimeoutError:
        return "Error: tool execution timed out"  # ← 自由字符串
    except Exception as exc:
        return f"Error executing tool: {exc}"       # ← 自由字符串

# ToolRouter.call() 改造后
async def call(
    self, full_name: str, arguments: dict, timeout=30.0
) -> Result[str, ToolError]:
    ...
    try:
        result = await self._execute(...)
        return Ok(result)
    except TimeoutError:
        return Err(ToolError(ToolErrorCode.TIMEOUT, f"Tool {full_name} timed out"))
    except Exception as exc:
        return Err(ToolError(ToolErrorCode.EXECUTION_ERROR, str(exc)))
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `gateway/result.py` | Result / Ok / Err 类型 | 核心基础设施 |
| **新增** `gateway/errors.py` | ProviderErrorCode / ToolErrorCode | 闭路错误码枚举 |
| 修改 `skills/router.py` | `call()` 返回 `Result[str, ToolError]` | 类型化错误 |
| 修改 `gateway/server.py` | `_resolve_tool_calls` | 处理 Result 类型 |
| 修改 `providers/*.py` | `chat()` 异常转换 | 抛出 ProviderError 而非自由字符串 |

---

## 8. 网关 RPC 方法注册表

### 背景

当前 `server.py` 的 `_process_message` 使用局部 `handlers` 字典，每新增一个命令都需要修改这个大方法。OpenClaw 用装饰器 + 注册表模式，新增 handler 只需在函数上挂装饰器。

### 设计目标

- 新增 WebSocket 命令零侵入 `_process_message`
- handler 可声明所需权限/scope（为 future 准备）
- 支持 handler 分组（connect、agent、channels、chat...）

### 接口定义

```python
# src/aipet/gateway/registry.py

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

HandlerFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


class GatewayRegistry:
    """Decorator-based registry for WebSocket message handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFn] = {}

    def register(
        self, method: str, scope: str | None = None
    ) -> Callable[[HandlerFn], HandlerFn]:
        def decorator(fn: HandlerFn) -> HandlerFn:
            self._handlers[method] = fn
            fn._gateway_method = method      # type: ignore[attr-defined]
            fn._gateway_scope = scope        # type: ignore[attr-defined]
            return fn
        return decorator

    def get(self, method: str) -> HandlerFn | None:
        return self._handlers.get(method)

    def merge(self, *registries: GatewayRegistry) -> None:
        for reg in registries:
            self._handlers.update(reg._handlers)


# 全局单例
registry = GatewayRegistry()
```

### 使用方式

```python
# src/aipet/gateway/server.py

from aipet.gateway.registry import registry

class Gateway:
    def __init__(self, ...):
        ...
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Auto-discover all decorated handlers on this instance."""
        for name in dir(self):
            obj = getattr(self, name)
            if callable(obj) and hasattr(obj, "_gateway_method"):
                method = getattr(obj, "_gateway_method")
                self._handlers[method] = obj

    # 原 handlers 字典中的方法改为装饰器定义
    @registry.register("client.hello", scope="connect")
    async def _handle_hello(self, client_id: str, payload: dict[str, Any]) -> None:
        ...

    @registry.register("chat.stream", scope="chat")
    async def _handle_chat_stream(self, client_id: str, payload: dict[str, Any]) -> None:
        ...

    # _process_message 简化
    async def _process_message(self, client_id: str, raw_message: str | bytes) -> None:
        ...
        handler = self._handlers.get(method)
        ...
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `gateway/registry.py` | GatewayRegistry 类 | 装饰器注册表 |
| 修改 `gateway/server.py` | 移除局部 handlers 字典 | 改用实例级别的 `_handlers` + 装饰器 |
| 修改 `gateway/server.py` | 所有 `_handle_*` 方法加 `@registry.register` | 自动注册 |

---

## 9. 会话元数据快照

### 背景

AI 对话进行中时，用户可能切换 Provider、删除 Skill、修改配置，导致当前轮次上下文不一致。OpenClaw 在每次 Agent Attempt 前保存技能快照和 Provider 配置快照。

### 设计目标

- 每次 `chat.stream` 开始时捕获 Skill 注册表和 Provider 状态的不可变快照
- 当前轮次始终使用快照，不受运行时配置变更影响
- 快照可用于调试和审计

### 接口定义

```python
# src/aipet/gateway/snapshot.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aipet.gateway.providers.manager import ProviderEntry


@dataclass(frozen=True)
class SkillSnapshot:
    """Immutable snapshot of skills at a point in time."""

    skill_ids: tuple[str, ...]
    tool_names: tuple[str, ...]
    schemas: dict[str, Any]  # tool_name -> schema dict


@dataclass(frozen=True)
class ProviderSnapshot:
    """Immutable snapshot of provider configuration."""

    entry: ProviderEntry
    model: str
    auth_profile_id: str | None = None


@dataclass(frozen=True)
class SessionSnapshot:
    """Complete runtime snapshot for a single AI turn."""

    session_id: str
    skills: SkillSnapshot
    provider: ProviderSnapshot
    soul_md_hash: str  # hash of soul.md content at snapshot time
    captured_at: str  # ISO timestamp


class SnapshotCapture:
    """Utility to capture runtime snapshots."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    def capture(self, session_id: str) -> SessionSnapshot:
        import hashlib
        from datetime import UTC, datetime

        skills = self._gateway.skills
        pm = self._gateway.provider_manager
        provider = pm.get_provider()

        # Capture skill state
        tool_schemas = {}
        for full_name in skills.list_tools():  # 假设有 list_tools()
            schema = skills.get_tool_schema(full_name)
            if schema:
                tool_schemas[full_name] = schema

        skill_snap = SkillSnapshot(
            skill_ids=tuple(skills._skills.keys()),
            tool_names=tuple(tool_schemas.keys()),
            schemas=tool_schemas,
        )

        # Capture provider state
        prov_snap = ProviderSnapshot(
            entry=provider.model_copy() if provider else ProviderEntry(id="echo", name="Echo", type="echo"),
            model=pm.current_model or "echo",
        )

        # Hash soul.md
        soul = self._gateway._read_soul()
        soul_hash = hashlib.sha256(soul.encode()).hexdigest()[:16]

        return SessionSnapshot(
            session_id=session_id,
            skills=skill_snap,
            provider=prov_snap,
            soul_md_hash=soul_hash,
            captured_at=datetime.now(UTC).isoformat(),
        )
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `gateway/snapshot.py` | SessionSnapshot / SnapshotCapture | 快照基础设施 |
| 修改 `gateway/server.py` | `_handle_chat_stream` 开头 | `snapshot = self._snapshot_capture.capture(session_id)` |
| 修改 `gateway/server.py` | `_build_ai_messages` | 使用 `snapshot.skills` 而非实时 `self.skills` |
| 修改 `skills/registry.py` | 新增 `list_tools()` / `get_tool_schema()` | 支持快照遍历 |

---

## 10. 子代理协作

### 背景

复杂任务（如编写 skill、多步骤数据分析）需要多轮工具调用和大量上下文，污染主会话。OpenClaw 的 `subagent.run()` 允许在工具执行中产生子代理会话，主会话只接收最终结果。

### 设计目标

- 工具可启动一个"子会话"执行复杂子任务
- 子会话有独立的上下文窗口，不污染主会话
- 子会话结果通过工具返回值回传主会话
- 支持子会话间通信（future）

### 接口定义

```python
# src/aipet/gateway/subagent.py

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aipet.gateway.providers.ai import Chunk, Message


@dataclass
class SubagentSession:
    """An isolated sub-session for delegated tasks."""

    id: str
    parent_session_id: str
    goal: str  # The task description
    messages: list[Message] = field(default_factory=list)
    max_turns: int = 10
    completed: bool = False
    result: str = ""


class SubagentManager:
    """Manages child agent sessions."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway
        self._sessions: dict[str, SubagentSession] = {}

    def create(
        self,
        parent_session_id: str,
        goal: str,
        max_turns: int = 10,
    ) -> SubagentSession:
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        session = SubagentSession(
            id=sub_id,
            parent_session_id=parent_session_id,
            goal=goal,
            max_turns=max_turns,
        )
        # Seed with system prompt and goal
        session.messages.append(
            Message(role="system", content="You are a specialized sub-agent. Focus only on the given task.")
        )
        session.messages.append(Message(role="user", content=goal))
        self._sessions[sub_id] = session
        return session

    async def run(self, sub_id: str) -> str:
        """Execute the subagent to completion and return result."""
        session = self._sessions.get(sub_id)
        if not session:
            raise ValueError(f"Subagent session {sub_id} not found")

        provider = self._gateway.provider_manager.create_ai_provider()
        full_text = ""

        for turn in range(session.max_turns):
            full_text = ""
            async for chunk in provider.chat(session.messages):
                full_text += chunk.delta

            # Check if the subagent produced tool calls
            tool_calls = self._gateway._parse_tool_calls(full_text)
            if tool_calls:
                # Execute tools in subagent context
                for tc in tool_calls:
                    name = tc.get("name", "")
                    args = tc.get("arguments", {})
                    try:
                        result = await self._gateway.tool_router.call(name, args)
                    except Exception as exc:
                        result = f"Error: {exc}"
                    session.messages.append(
                        Message(role="tool", content=result, tool_call_id=tc.get("id", ""))
                    )
            else:
                # Subagent completed without further tool calls
                session.completed = True
                session.result = full_text.strip()
                return session.result

        # Max turns reached
        session.result = full_text.strip() or "[Subagent reached max turns without completion]"
        return session.result

    def get_history(self, sub_id: str) -> list[Message]:
        session = self._sessions.get(sub_id)
        return session.messages.copy() if session else []
```

### 暴露为 Skill 工具

```python
# builtin_skills/subagent/__init__.py

from aipet.gateway.subagent import SubagentManager

# 这个 skill 需要 gateway 实例注入，或通过 context 获取
# 实际实现时通过 ToolContext 注入 gateway.subagent_manager

async def run_subagent(ctx, task: str, max_turns: int = 10) -> str:
    """Delegate a complex task to a subagent and return the result."""
    manager: SubagentManager = ctx.gateway.subagent_manager
    session = manager.create(ctx.session_id, task, max_turns)
    return await manager.run(session.id)


tools = {
    "run_subagent": run_subagent,
}
```

### 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| **新增** `gateway/subagent.py` | SubagentSession / SubagentManager | 子代理核心 |
| **新增** `builtin_skills/subagent/` | run_subagent 工具 | 通过 skill 暴露给 AI |
| 修改 `gateway/server.py` | `__init__` | 初始化 `self.subagent_manager = SubagentManager(self)` |
| 修改 `gateway/server.py` | `_process_message` | 新增 `subagent.create/run/history` 协议命令 |
| 修改 `skills/factory.py` | ToolContext 注入 gateway | 子代理 skill 可访问 gateway 实例 |

---

## 实施优先级建议

基于改动范围、风险和收益，建议按以下顺序实施：

| 优先级 | 改进 | 预估工时 | 风险 |
|--------|------|---------|------|
| P0 | **#7 结果类型与闭路错误码** | 4h | 低（纯类型层，不影响运行时） |
| P0 | **#5 Prompt 缓存稳定性** | 1h | 极低 |
| P0 | **#6 延迟加载** | 2h | 低 |
| P1 | **#3 工具结果截断** | 2h | 低 |
| P1 | **#8 网关 RPC 注册表** | 3h | 低 |
| P1 | **#9 会话元数据快照** | 3h | 中（需改 server.py 核心流程） |
| P2 | **#1 Provider Stream Wrapper** | 6h | 中（重构所有 Provider） |
| P2 | **#2 认证档案轮换+故障转移** | 8h | 中（涉及配置格式变更） |
| P2 | **#4 工具工厂模式** | 4h | 中 |
| P3 | **#10 子代理协作** | 6h | 中高（新架构概念） |

---

*文档版本: v1.0-drafts*
*待确认后按此设计编码实现。*
