# AIPet v2.0 架构设计文档

> **状态**: 草案 (Draft)  
> **版本**: 0.1  
> **日期**: 2026-04-14  
> **目标读者**: 开发团队、技术评审委员会  

---

## 1. 项目背景与现状分析

### 1.1 现有系统概况

当前 `AIPet` 是一个基于 Python + PyQt5 的 Live2D AI 桌面宠物应用，核心功能包括：
- Live2D 角色渲染与口型同步
- AI 聊天（流式响应）
- TTS 语音合成与播放
- ASR 语音输入
- WebSocket 日志推送与游戏伴侣功能
- 插件系统（命令解析器模式）

### 1.2 现有系统致命缺陷

经过全面代码审查，现有系统存在以下**架构级问题**：

| 问题类别 | 具体表现 | 影响程度 |
|---------|---------|---------|
| **神类（God Object）** | `AppController` 长达 2600+ 行，直接管理 GUI、AI、TTS、ASR、Live2D、HTTP 服务器、插件等所有模块 | 🔴 致命 |
| **UI 与业务深度耦合** | `ChatWindow` 直接调用 `AppController._open_tts_settings_dialog()` 等私有方法 | 🔴 致命 |
| **并发模型混乱** | 混合使用 `threading`、`pyqtSignal`、`Lock`、`Event`，死锁和竞态风险极高 | 🔴 致命 |
| **状态管理无单一数据源** | 状态散落在 JSON 文件、`AppController` 属性、`DataManager` 中，自相矛盾 | 🟠 严重 |
| **硬编码绝对路径** | 多处写死 `E:\live2d\...`，无法迁移 | 🟠 严重 |
| **无依赖管理文件** | 没有 `requirements.txt` 或 `pyproject.toml` | 🟡 中等 |
| **大量垃圾文件堆积** | 根目录存在 7 个 `main copy*.py`、15 个 `debug_*.py`、6 个 `fix_*.py` | 🟡 中等 |

### 1.3 设计决策

**结论**: 现有代码库的架构腐化已经到了"改一行、崩三处"的程度。考虑到项目规模、维护成本和未来扩展性，**不建议在现有代码上修补，建议另起炉灶（v2.0）**，同时借鉴当前最先进的开源架构思想（如 OpenClaw）。

---

## 2. 设计目标与原则

### 2.1 核心设计目标

1. **解耦**: UI 层与业务核心彻底分离，业务逻辑可以在无 GUI 环境下运行和测试。
2. **扩展性**: 新增 AI 提供商、TTS 引擎、Live2D 模型、技能插件时，无需修改核心代码。
3. **可维护性**: 单一模块职责清晰，代码行数受控（核心类不超过 500 行）。
4. **可测试性**: 每个 Service 都可以独立进行单元测试。
5. **本地化优先**: 所有配置、记忆、聊天记录默认保存在本地。
6. **多前端支持**: 不仅支持 PyQt Live2D 桌面宠物，未来可支持 Web 页面、手机遥控器等客户端。

### 2.2 设计原则

- **Gateway 模式**: 借鉴 OpenClaw 的 Gateway + Client 架构，核心大脑（Gateway）作为常驻后台服务，UI 只是它的一个客户端。
- **事件驱动**: 模块间不直接持有对方引用，通过类型安全的事件总线通信。
- **协议优先**: Gateway 与 Client 之间通过明确的 WebSocket JSON-RPC 协议通信。
- **文档即代码（Markdown-Driven Skills）**: 技能插件通过 `SKILL.md` 自描述，AI 通过阅读文档学习如何调用工具。
- **异步优先**: IO 密集型操作（AI 请求、TTS、WebSocket）全部使用 `async/await`。

---

## 3. 总体架构

### 3.1 架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AIPet v2.0 Architecture                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 3: Frontend Clients                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  PyQt6 Live2D   │  │  WebChat        │  │  Mobile Node    │             │
│  │  (桌面宠物主入口) │  │  (浏览器/远程)   │  │  (iOS/Android)  │             │
│  │                 │  │                 │  │                 │             │
│  │ • 透明无边框窗口 │  │ • 远程聊天       │  │ • 拍照/截图     │             │
│  │ • Live2D 渲染   │  │ • 状态监控       │  │ • 系统通知      │             │
│  │ • 口型同步      │  │                 │  │                 │             │
│  │ • Live Canvas   │  │                 │  │                 │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
│           │                    │                    │                      │
│           └────────────────────┴────────────────────┘                      │
│                                WebSocket / JSON-RPC                         │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 2: AIPet Gateway (Python asyncio)                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Core Runtime                                                       │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │   │
│  │  │ EventBus │  │ Session  │  │ State    │  │ Config   │            │   │
│  │  │ (typed)  │  │ Manager  │  │ Store    │  │ Manager  │            │   │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘            │   │
│  │       └─────────────┴─────────────┴─────────────┘                  │   │
│  │                              │                                      │   │
│  │  Service Providers (Pluggable)                                     │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │   │
│  │  │ AI       │  │ TTS      │  │ ASR      │  │ Live2D   │            │   │
│  │  │ Provider │  │ Provider │  │ Provider │  │ Provider │            │   │
│  │  │ Interface│  │ Interface│  │ Interface│  │ Interface│            │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │   │
│  │                                                                     │   │
│  │  Skill Engine                                                       │   │
│  │  ┌─────────────────────────────────────────────────────────────┐   │   │
│  │  │  Skill Registry  │  Tool Router  │  Sandbox Executor       │   │   │
│  │  └─────────────────────────────────────────────────────────────┘   │   │
│  │                                                                     │   │
│  │  Media Pipeline                                                     │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                        │   │
│  │  │ Audio    │  │ Canvas   │  │ File     │                        │   │
│  │  │ Player   │  │ Manager  │  │ Manager  │                        │   │
│  │  └──────────┘  └──────────┘  └──────────┘                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 1: Data & Assets                                                     │
│  ~/.aipet/                                                                  │
│  ├── gateway.toml              (网关配置)                                   │
│  ├── soul.md                   (角色人格 / 系统提示)                         │
│  ├── memory.md                 (长期记忆)                                   │
│  ├── tools.md                  (动态生成的工具定义)                          │
│  ├── sessions/                 (Session 隔离数据)                           │
│  │   └── <session_id>/chat.db  (SQLite 聊天记录)                            │
│  ├── skills/                   (技能插件目录)                               │
│  │   ├── weather/                                                         │
│  │   │   ├── SKILL.md                                                     │
│  │   │   └── weather.py                                                   │
│  │   └── browser/                                                         │
│  │       ├── SKILL.md                                                     │
│  │       └── browser.py                                                   │
│  └── models/                   (Live2D 模型文件)                            │
│      └── ganyu/                                                            │
│          └── ganyu.model3.json                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 关键架构决策说明

#### 决策 1: 为什么采用 Gateway + Client 架构？

传统桌面应用（如现有 AIPet）将所有逻辑放在 UI 进程里，导致：
- UI 崩溃 = 业务全丢
- 无法独立测试 AI 逻辑
- 无法支持远程控制或多端协同

Gateway 架构的优势：
- **稳定性**: Gateway 作为守护进程常驻后台，即使 PyQt 前端崩溃重启，聊天记录和状态不丢失。
- **可测试性**: Gateway 是纯 Python asyncio 服务，可以脱离 GUI 进行完整的单元测试和集成测试。
- **扩展性**: 未来添加 WebChat、手机 App、甚至接入智能家居（作为 Node）只需实现同一个 WebSocket 协议。
- **资源管理**: Live2D 渲染是 GPU 密集型，可以把它放在前端；AI 推理和文件操作放在 Gateway，职责分离。

#### 决策 2: 为什么不用 MCP（Model Context Protocol）而采用自研 Skill Engine？

MCP 是 Anthropic 推出的标准协议，优势是生态丰富、沙箱化。但我们评估后认为：
- **复杂度**: MCP 需要为每个 Skill 启动独立子进程（stdio 或 SSE），对于桌面应用来说进程管理过重。
- **性能**: 高频调用的 Skill（如天气查询、计算器）如果每次都要进程间通信，延迟不可接受。
- **生态**: Python 桌面宠物的 Skill 大多是本地文件操作、截图、浏览器控制，不需要复用大量外部 MCP Server。

**折中方案**: 自研轻量级 Skill Engine（Python 函数直接调用），但接口设计上**兼容 MCP 的精神**（工具描述用结构化 Schema，支持自动发现）。未来如果某个外部 MCP Server 非常有价值，可以在 Skill Engine 中加一个 **MCP Adapter** 进行桥接。

#### 决策 3: 为什么继续使用 `live2d-py` 而不是换 Web 版 Live2D？

`live2d-py` 是目前 Python 生态中唯一成熟的 Cubism 渲染方案。Web 版 Live2D 虽然特效更新，但：
- 做"无边框透明窗口"、"鼠标穿透"、"始终置顶"在 Windows 上需要大量 Native Hook
- 与 Python 异步生态的桥接复杂

**策略**: 继续使用 `live2d-py`，但将其严格封装在 `Live2DProvider` 接口后面，且**只放在 PyQt Client 中**，Gateway 对此无感知。未来如果有更好的 Python Live2D 库，替换成本低。

---

## 4. 核心子系统设计

### 4.1 Gateway WebSocket 协议

Gateway 监听 `ws://127.0.0.1:18790`（默认端口），所有客户端通过 WebSocket 连接。协议采用 **JSON-RPC 2.0** 风格的请求/通知 + **Server-Sent Events** 风格的推送。

#### 4.1.1 消息基类

```json
{
  "id": "uuid-v4",
  "type": "request | response | notification | event",
  "method": "method.name",
  "payload": {}
}
```

#### 4.1.2 关键方法定义

**Client -> Gateway (Request)**

| Method | 说明 | Payload 示例 |
|--------|------|-------------|
| `chat.send` | 发送聊天消息 | `{"session_id": "main", "content": "你好", "attachments": []}` |
| `chat.stream` | 发送消息并请求流式响应 | 同上 |
| `session.create` | 创建新 Session | `{"name": "工作助手", "soul": "soul.md"}` |
| `session.list` | 列出所有 Session | `{}` |
| `tts.speak` | 请求 TTS 播放 | `{"text": "你好", "voice_id": "default"}` |
| `tool.call` | 直接调用某个工具 | `{"skill": "weather", "tool": "get_weather", "args": {"city": "北京"}}` |
| `canvas.push` | 向前端推送 Canvas 内容 | `{"element": "image", "src": "data:image/png;base64,..."}` |
| `system.shutdown` | 关闭 Gateway | `{}` |

**Gateway -> Client (Event / Notification)**

| Method | 说明 | Payload 示例 |
|--------|------|-------------|
| `chat.stream.start` | AI 流式响应开始 | `{"message_id": "msg_123"}` |
| `chat.stream.chunk` | AI 流式响应片段 | `{"message_id": "msg_123", "delta": "你好"}` |
| `chat.stream.end` | AI 流式响应结束 | `{"message_id": "msg_123", "finish_reason": "stop"}` |
| `chat.message` | 完整消息（非流式） | `{"message_id": "msg_123", "role": "assistant", "content": "..."}` |
| `tts.start` | TTS 开始播放 | `{"text": "你好"}` |
| `tts.end` | TTS 播放结束 | `{}` |
| `live2d.expression` | 触发 Live2D 表情 | `{"expression": "happy"}` |
| `live2d.motion` | 触发 Live2D 动作 | `{"motion": "tap_body", "priority": 3}` |
| `canvas.show` | 显示 Canvas 元素 | `{"type": "card", "data": {...}}` |
| `system.error` | 系统错误通知 | `{"code": "AI_PROVIDER_ERROR", "message": "..."}` |

#### 4.1.3 连接与认证

由于是纯本地应用，认证采用**极简模式**：
- Gateway 绑定 `127.0.0.1`，拒绝外部连接。
- 客户端连接时发送 `client.hello`，携带 `client_type`（`pyqt`, `web`, `mobile`）和 `version`。
- Gateway 维护一个 `ClientSession` 列表，支持多客户端同时在线。


### 4.2 类型安全事件总线 (Typed EventBus)

Gateway 内部模块之间禁止直接引用，全部通过 `EventBus` 通信。我们使用 `pydantic` 定义事件类型，确保编译期（静态分析）级别的类型安全。

```python
from pydantic import BaseModel
from typing import TypeVar, Generic, Callable, Awaitable

T = TypeVar("T", bound=BaseModel)

class EventBus:
    def subscribe(self, event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> None: ...
    async def emit(self, event: T) -> None: ...
```

**核心事件类型示例**：

```python
class ChatMessageReceived(BaseModel):
    session_id: str
    message_id: str
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: datetime

class AIStreamChunk(BaseModel):
    session_id: str
    message_id: str
    delta: str

class TTSPlaybackStarted(BaseModel):
    session_id: str
    text: str
    audio_url: str | None  # 本地文件路径或 base64

class ToolExecutionRequested(BaseModel):
    session_id: str
    skill_id: str
    tool_id: str
    arguments: dict
```

**设计理由**：
- 替代 `pyqtSignal` 的字符串事件名，避免 `EventTypes.CHAT_WINDOW_SHOWN` 这类运行时错误。
- 事件即数据，天然支持日志追踪和回放。
- 异步 Handler 可以链式组合，避免回调地狱。

### 4.3 Session 管理器

Session 是 Gateway 中隔离对话上下文的最小单元。一个 Session 对应一个独立的 AI 上下文窗口、一套独立的记忆和工具权限。

**Session 模型**：

```python
class Session(BaseModel):
    id: str  # UUID
    name: str
    created_at: datetime
    updated_at: datetime
    soul_path: str  # 指向 soul.md 文件
    memory_snapshot: str  # 最近一次压缩后的记忆摘要
    model_config: ModelConfig
    tool_allowlist: list[str]  # 允许调用的 skill:tool 列表
    context_window: int  # 当前上下文 token 数（估算）
```

**Session Manager 职责**：
1. **生命周期管理**: 创建、存档、删除 Session。
2. **上下文组装**: 每次调用 AI 前，组装 `System Prompt` + `Memory` + `Recent Messages`。
3. **Token 预算管理**: 当上下文接近模型上限时，触发 `ContextCompaction`（压缩早期对话为摘要）。
4. **权限隔离**: 不同 Session 可以绑定不同的 Skill 集合（例如"工作 Session"可以访问浏览器和文件，"闲聊 Session"只能聊天）。

**Context Compaction 策略**：
- 保留最近的 10 轮完整对话。
- 更早的对话交给 AI 生成 200 字摘要，写入 `memory.md`。
- 摘要作为系统提示的一部分注入。

### 4.4 状态存储 (State Store)

采用 **CQRS-lite** 模式：
- **写操作**: 通过 `StateStore.dispatch(action)` 进行，保证所有状态变更有序、可追踪。
- **读操作**: UI 和 Service 订阅状态变化，或主动查询快照。

```python
class StateStore:
    def __init__(self, initial_state: AppState):
        self._state = initial_state
        self._subscribers: list[Callable[[AppState], Awaitable[None]]] = []

    async def dispatch(self, action: Action) -> None:
        self._state = self._reducer(self._state, action)
        await self._notify()
```

**AppState 结构**：

```python
class AppState(BaseModel):
    gateway_version: str
    current_session_id: str | None
    sessions: dict[str, Session]
    clients: dict[str, ClientInfo]  # 当前连接的客户端
    ai_provider_status: ProviderStatus
    tts_status: ProviderStatus
    asr_status: ProviderStatus
    active_skills: list[str]
    notifications: list[Notification]
```

**设计理由**：
- 旧项目的状态散落在 `AppController`、`aipet_state.json`、`ChatWindow` 中，导致"界面显示聊天窗口已关闭，但状态文件说已打开"的 bug。
- 单一 `AppState` 是 Gateway 的**唯一事实来源**，所有客户端看到的状态完全一致。

### 4.5 AI Provider 抽象层

AI Provider 负责与具体的大模型 API 通信，屏蔽厂商差异。

```python
class AIProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def supports_tool_calling(self) -> bool: ...

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> AsyncIterator[Chunk]: ...

    async def compact(self, messages: list[Message]) -> str: ...
```

**内置实现**：
- `GeminiProvider`: Google Gemini API（默认）
- `OpenAIProvider`: OpenAI / Azure API
- `AnthropicProvider`: Claude API
- `OllamaProvider`: 本地模型（通过 Ollama HTTP API）

**工具调用流程**：
1. Gateway 从 Skill Registry 收集所有可用 Tool，生成 OpenAI 风格的 `tools` 列表。
2. 调用 `AIProvider.chat(messages, tools)`。
3. 如果 AI 返回 `tool_calls`，Gateway 暂停流式输出，执行 `ToolEngine.call(...)`。
4. 将工具执行结果以 `function` role 的消息追加到上下文，再次调用 AI 获取最终回复。
5. 整个 Tool Call 过程对前端透明，前端只会看到最终的 AI 回复流（或可选地看到"正在调用工具"的提示）。

### 4.6 TTS / ASR Provider 抽象层

#### TTS Provider

```python
class TTSProvider(Protocol):
    async def synthesize(self, text: str, voice_id: str | None = None) -> AudioSegment: ...
    def list_voices(self) -> list[VoiceInfo]: ...
```

**内置实现**：
- `EdgeTTSProvider`: 微软 Edge TTS，免费、中文自然、延迟低。
- `GPTSoVITSProvider`: 通过本地 HTTP API 调用 GPT-SoVITS，支持角色声音克隆。
- `SystemTTSProvider`: 系统自带 TTS（降级方案）。

#### 音频播放器 (Audio Pipeline)

TTS 和音频播放分离：
- `TTSProvider` 只负责"文字 -> 音频字节/文件"。
- `AudioPlayer` 负责"音频文件 -> 扬声器输出"，并管理播放队列。

```python
class AudioPlayer:
    async def enqueue(self, audio_path: str, on_start: Callable, on_end: Callable) -> None: ...
```

**口型同步集成**：
- 音频播放开始时，`AudioPlayer` 发出 `TTSPlaybackStarted` 事件。
- PyQt Client 订阅此事件，启动 Live2D 口型同步（基于音频波形分析或简单计时器）。
- 音频播放结束时，发出 `TTSPlaybackEnded`，Client 停止口型同步。

#### ASR Provider

ASR 作为**可选扩展**，不阻塞核心架构。

```python
class ASRProvider(Protocol):
    async def transcribe(self, audio_path: str) -> str: ...
    async def listen_stream(self) -> AsyncIterator[str]: ...  # 实时语音输入
```

**内置实现**：
- `FasterWhisperProvider`: 本地运行，准确率高。
- `FunASRProvider`: 阿里达摩院，中文支持好。

### 4.7 Skill Engine（技能引擎）

这是 v2.0 最具特色的设计。我们不使用传统"硬编码插件"，而是采用 **Markdown 文档驱动 + Python 函数绑定**。

#### 目录结构

```
~/.aipet/skills/
├── weather/
│   ├── SKILL.md
│   └── __init__.py
├── browser/
│   ├── SKILL.md
│   └── __init__.py
└── screenshot/
    ├── SKILL.md
    └── __init__.py
```

#### SKILL.md 规范

```markdown
# Weather Skill

## Description
查询指定城市的实时天气。

## Tools
- `get_weather(city: str) -> dict`
  - 参数 `city`: 城市名称，如 "北京"
  - 返回: `{"temperature": 25, "condition": "晴"}`

## Examples
用户: "北京今天天气怎么样？"
→ 调用 `get_weather("北京")`
```

#### 运行时流程

1. **扫描阶段**: Gateway 启动时扫描 `skills/` 目录，读取所有 `SKILL.md`。
2. **注册阶段**: 解析 `SKILL.md` 中的 Tool 定义，生成结构化 Schema（OpenAI Function Calling 格式）。
3. **注入阶段**: 将所有 Tool Schema 和 `SKILL.md` 的 `Description` 合并成 `TOOLS.md`，在每次 AI 调用时注入到系统提示中。
4. **执行阶段**: 当 AI 返回 `tool_calls` 时，`ToolRouter` 根据 `skill_id.tool_id` 定位到对应的 Python 函数，并在受控环境中执行。

#### 安全沙箱

Skill 的 Python 代码运行在**当前 Python 进程**中（轻量级），但通过以下机制限制风险：
- **权限白名单**: 每个 Skill 在 `SKILL.md` 中声明所需权限（`filesystem:read`, `network`, `browser`, `system:exec`）。
- **路径隔离**: Skill 默认只能读写 `~/.aipet/skills/<skill_name>/workspace/` 目录。
- **网络隔离**: 未声明 `network` 权限的 Skill 禁止发起 HTTP 请求（通过 monkey-patch `urllib` / `httpx` 实现）。

**注意**: 这不是操作系统级别的沙箱，而是**应用层沙箱**。如果未来安全要求提高，可以升级为子进程沙箱（如 `subprocess` + `seccomp` 或 Docker）。

### 4.8 Live2D Provider

Live2D 渲染严格限定在 **PyQt Client** 中，Gateway 对此一无所知。Gateway 只是向前端发送抽象的"表情/动作"事件。

**PyQt Client 中的 Live2D 职责**：
- 使用 `live2d-py` 在 `QOpenGLWidget` 中渲染模型。
- 订阅 Gateway 的 `live2d.expression` 和 `live2d.motion` 事件。
- 管理 TTS 口型同步（`TTSPlaybackStarted` / `TTSPlaybackEnded`）。
- 处理鼠标交互（点击、拖拽），并将交互事件（如"用户戳了一下"）发送给 Gateway，Gateway 可以决定让 AI 做出什么反应。

**模型加载策略**：
- 模型文件存储在 `~/.aipet/models/` 下。
- 支持运行时热切换模型（Gateway 发送 `live2d.load_model` 事件）。


## 5. 前端设计：PyQt Live2D Client

PyQt Client 是用户的主要交互入口，但它**不是业务核心**，只是 Gateway 的可视化皮肤。

### 5.1 进程模型

```
[OS]
  ├── [Gateway Process]  python -m aipet.gateway  (常驻后台)
  └── [PyQt Client]      python -m aipet.frontend  (用户可见)
```

**启动顺序**：
1. 用户双击 `AIPet.exe`。
2. Launcher 先检查 Gateway 是否已在运行，若未运行则启动 Gateway（隐藏窗口）。
3. Launcher 启动 PyQt Client，Client 通过 WebSocket 连接到 Gateway。
4. 如果 Client 崩溃，用户可以单独重启 Client，不影响 Gateway。

### 5.2 窗口架构

PyQt Client 包含两类窗口：

#### A. Live2D 角色窗口（主窗口）

- **无边框 + 透明背景** (`Qt.FramelessWindowHint`, `Qt.WA_TranslucentBackground`)
- **始终置顶** (`Qt.WindowStaysOnTopHint`)
- **鼠标穿透**（当用户不与之交互时，点击穿透到桌面）
- **边缘吸附**（拖到屏幕边缘时自动贴边）
- **内容**: `QOpenGLWidget` 渲染 Live2D 模型

**交互行为**：
- 鼠标悬停: 显示快捷操作栏（静音、设置、隐藏）
- 拖拽: 移动窗口位置
- 右键点击: 打开菜单（切换模型、打开聊天窗、退出）
- 双击 / 戳一下: 发送 `live2d.interaction` 事件到 Gateway，AI 可能回复"哎呀，别戳我~"

#### B. 聊天窗口（Chat Window）

- **现代气泡式 UI**（类似微信 / Telegram）
- 支持 Markdown 渲染、代码高亮
- 支持图片、附件拖拽上传
- 底部输入框支持多行文本、Shift+Enter 换行、Enter 发送
- 顶部显示当前 Session 名称和模型信息

**与 Gateway 的通信**：
- 用户点击"发送" -> Client 发送 `chat.stream` 请求到 Gateway
- Gateway 返回 `chat.stream.start` -> Client 显示"正在输入"动画
- Gateway 返回 `chat.stream.chunk` -> Client 追加到 AI 气泡中
- Gateway 返回 `chat.stream.end` -> Client 隐藏"正在输入"，保存消息到本地 SQLite

### 5.3 Live Canvas（A2UI）

Live Canvas 是 AI 主动弹出的小型浮动面板，用于展示非文本内容。

**触发方式**：Gateway 发送 `canvas.show` 事件。

**支持的 Canvas 类型**：

| Type | 说明 | 示例 |
|------|------|------|
| `image` | 显示图片 | AI 生成的图片 |
| `card` | 结构化卡片 | 天气信息、股票行情 |
| `list` | 列表 | 待办事项、日程安排 |
| `code` | 代码块 | 带复制按钮的代码片段 |
| `web` | 内嵌网页 | 简单的 HTML 渲染 |

**实现方式**：
- 在 Live2D 窗口旁边浮动一个 `QWidget`。
- 内容用 `QWebEngineView`（轻量 HTML）或原生 `QWidget` 渲染。
- 支持自动关闭（5 秒后淡出）或手动关闭。

### 5.4 系统托盘

PyQt Client 最小化到系统托盘，托盘菜单提供：
- 显示 / 隐藏 Live2D
- 显示 / 隐藏聊天窗口
- 切换 Session
- 打开设置
- 退出 Gateway & Client

---

## 6. 数据模型与持久化

### 6.1 配置管理

采用 `pydantic-settings` + TOML 格式，配置文件位于 `~/.aipet/gateway.toml`。

```toml
[gateway]
bind = "127.0.0.1"
port = 18790
log_level = "INFO"

[ai]
provider = "gemini"
model = "gemini-2.5-flash-preview-05-20"
api_key = "${GEMINI_API_KEY}"  # 支持环境变量引用
max_context_tokens = 128000

[tts]
provider = "edge-tts"
default_voice = "zh-CN-XiaoxiaoNeural"
auto_play = true

[asr]
provider = "faster-whisper"
enabled = false

[live2d]
default_model = "ganyu"
auto_show = true

[skills]
auto_load = true
allowed_skills = ["*"]  # * 表示允许所有
```

**设计理由**：
- TOML 比 JSON 更适合人类编辑（支持注释、多行字符串）。
- `pydantic-settings` 提供类型校验、默认值、环境变量覆盖能力。

### 6.2 聊天记录

使用 **SQLite**（`aiosqlite` 异步库）。

**数据库位置**：`~/.aipet/sessions/<session_id>/chat.db`

**表结构**：

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- 'user', 'assistant', 'system', 'tool'
    content TEXT,
    tool_calls TEXT,     -- JSON array, for assistant messages with tool calls
    tool_call_id TEXT,   -- for tool role messages
    attachments TEXT,    -- JSON array of file paths
    model TEXT,          -- which AI model generated this
    tokens_used INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    soul_path TEXT,
    memory_summary TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
```

**为什么不用 JSON 文件**：
- 旧项目的 `aipet_user_data.json` 已达 212KB，JSON 不适合大量结构化数据的增量写入。
- SQLite 支持分页查询、全文搜索、事务安全。

### 6.3 记忆系统（Memory）

**短期记忆**: 最近 N 轮对话（从 SQLite 读取）。

**长期记忆**: 存储在 `~/.aipet/memory.md`，由 AI 自动维护。Gateway 在以下时机更新记忆：
- Session 结束时
- 上下文长度超过阈值时（Context Compaction）
- 用户明确说"请记住..."时

**memory.md 格式示例**：
```markdown
# Memory for Session "main"

- 用户喜欢下雨天，讨厌晴天。
- 用户的工作是软件工程师，擅长 Python。
- 用户有一个叫"小白"的宠物猫。
```

**人格（Soul）**: 存储在 `~/.aipet/soul.md`，由用户直接编辑。

```markdown
# Soul

你的名字是芊芊。你是一个温柔、有点宅的二次元少女。
你说话会带一些可爱的语气词，比如"呢"、"呀"。
你喜欢编程和打游戏，对天气很敏感。
```

每次调用 AI 时，Prompt 的组装顺序：
1. `SOUL.md`（人格定义）
2. `MEMORY.md`（长期记忆摘要）
3. `TOOLS.md`（可用工具说明）
4. 最近 10 轮对话（短期记忆）
5. 当前用户输入

---

## 7. 技术栈详细选型

### 7.1 必选核心栈

| 层级 | 技术 | 版本 | 选型理由 |
|------|------|------|---------|
| **语言** | Python | 3.12+ | 生态成熟，AI 库最全 |
| **异步运行时** | `asyncio` | 内置 | 标准库，与第三方库兼容性最好 |
| **Qt 绑定** | `PySide6` | 6.6+ | Qt 官方支持，LGPL 商用免费 |
| **异步桥接** | `qasync` | 0.27+ | 在 Qt 事件循环上跑 asyncio |
| **Live2D** | `live2d-py` | 0.5.x | Python 唯一成熟的 Cubism 绑定 |
| **WebSocket** | `websockets` | 12+ | 纯 asyncio，API 简洁 |
| **HTTP Client** | `httpx` | 0.27+ | 原生 async，比 aiohttp 更现代 |
| **数据校验** | `pydantic` | 2.7+ | 类型安全、序列化、配置管理一体化 |
| **配置管理** | `pydantic-settings` | 2.2+ | 环境变量、TOML、默认值统一管理 |
| **数据库** | `aiosqlite` | 0.20+ | asyncio 封装的 SQLite |
| **日志** | `structlog` | 24+ | 结构化日志，便于后续对接 ELK |

### 7.2 AI Provider 依赖

| Provider | 库 |
|----------|-----|
| Gemini | `google-generativeai` (可选) 或直接用 `httpx` 调用 REST API |
| OpenAI | `openai` (官方 async 支持) |
| Anthropic | `anthropic` (官方 async 支持) |
| Ollama | 直接用 `httpx` 调用本地 HTTP API |

### 7.3 TTS / ASR 依赖

| 功能 | 库 |
|------|-----|
| Edge TTS | `edge-tts` |
| 音频播放 | `sounddevice` + `soundfile` 或 `pyaudio` |
| Whisper | `faster-whisper` (需要 `ffmpeg`) |

### 7.4 开发工具

| 工具 | 用途 |
|------|------|
| `uv` | 极速依赖管理与虚拟环境 |
| `ruff` | 代码格式化和 Lint |
| `mypy` | 静态类型检查 |
| `pytest` | 单元测试 |
| `pytest-asyncio` | 异步测试支持 |

---

## 8. 部署与分发方案

### 8.1 开发环境

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆仓库
git clone <repo>
cd aipet-v2

# 创建虚拟环境并安装依赖
uv venv --python 3.12
uv pip install -e ".[dev]"
```

### 8.2 运行时模式

#### 模式 A: 开发模式（手动分别启动）

```bash
# Terminal 1: 启动 Gateway
python -m aipet.gateway

# Terminal 2: 启动 PyQt Client
python -m aipet.frontend
```

#### 模式 B: 生产模式（单入口自动拉起）

```bash
python -m aipet.launcher
```

`launcher` 的职责：
1. 读取 `gateway.toml`
2. 尝试连接 Gateway WebSocket，若失败则启动 Gateway 子进程
3. 启动 PyQt Client
4. 监听两个进程的健康状态，实现自动重启

### 8.3 Windows 打包

使用 **PyInstaller** 打包为单文件 `.exe`。

**目录结构**：
```
dist/
└── AIPet/
    ├── AIPet.exe          # Launcher
    ├── gateway/           # Gateway 代码和资源
    └── frontend/          # PyQt Client 代码和资源
```

**打包要点**：
- 必须包含 `live2d-py` 的 native DLL (`Live2DCubismCore.dll`)。
- 必须包含 OpenGL 运行库。
- 首次运行时，在 `%APPDATA%\AIPet\` 下创建 `.aipet/` 数据目录。

### 8.4 自动更新（未来规划）

- 使用 `pyupdater` 或自研增量更新：
  - Gateway 启动时检查更新服务器（GitHub Releases）。
  - 下载差分补丁到临时目录。
  - 下次启动时由 Launcher 完成文件替换。

---

## 9. 风险分析与替代方案

### 9.1 已知风险

| 风险 | 影响 | 发生概率 | 缓解措施 |
|------|------|---------|---------|
| `live2d-py` 停止维护 | 高 | 中 | 将 Live2D 逻辑严格封装在 Provider 接口后，未来可替换 |
| `qasync` 与某些 Qt 版本不兼容 | 中 | 低 | 锁定 Qt 和 qasync 版本，充分测试 |
| Gateway 进程管理复杂 | 中 | 中 | Launcher 实现进程守护和自动重启 |
| Windows 防火墙/杀毒误报 | 中 | 高 | 代码签名证书（未来）、安装向导引导用户放行 |
| PyInstaller 打包体积过大 | 低 | 高 | 精简依赖，使用 UPX 压缩，预计 150-250MB |

### 9.2 重大设计决策的替代方案

#### 替代方案 A: 放弃 Gateway，回到单进程

**优点**：开发周期缩短 30%，进程管理简单，资源占用略低。  
**缺点**：重蹈旧项目覆辙，UI 与业务再次耦合，无法支持远程客户端。  
**结论**：**不建议**，除非项目周期被压缩到 1 周以内。

#### 替代方案 B: 使用 Tauri 替代 PyQt

**优点**：UI 用 React/Vue 写，极其现代，Live2D Web SDK 特效更丰富。  
**缺点**：
- Windows 上实现"透明无边框桌面宠物"需要写大量 Rust + Win32 API Hook
- `live2d-py` 无法直接在 WebView 中使用，必须改用 Web Live2D SDK，模型兼容性问题未知
- 开发团队需要掌握 Rust

**结论**：如果团队前端实力强且对 Rust 不排斥，可以作为 v3.0 的候选。v2.0 仍建议用 PyQt。

#### 替代方案 C: 直接使用 MCP 标准

**优点**：直接复用社区成百上千的 MCP Server。  
**缺点**：
- 每个 MCP Server 都是独立进程，桌面应用上下文下太重
- 进程生命周期管理复杂
- 大多数 MCP Server 是 Node.js 写的，需要用户安装 Node 环境

**结论**：v2.0 采用自研轻量 Skill Engine，v2.x 或 v3.0 增加 MCP Adapter。

---

## 10. 开发路线图（MVP → v2.0）

### Phase 1: 骨架搭建（2 周）
- [ ] 项目脚手架（`pyproject.toml`、目录结构、`uv` 环境）
- [ ] Gateway WebSocket 服务器（基础连接、心跳、JSON-RPC 协议）
- [ ] PyQt Client 最小可运行版本（空白窗口 + WebSocket 连接状态显示）
- [ ] 事件总线（Typed EventBus）
- [ ] 配置管理（`pydantic-settings` + TOML）

### Phase 2: 核心能力（2 周）
- [ ] AI Provider 抽象层 + Gemini 实现
- [ ] Session Manager + SQLite 聊天记录
- [ ] Prompt 组装（SOUL.md + MEMORY.md + 历史对话）
- [ ] Chat Window UI（气泡、流式输出、Markdown 渲染）

### Phase 3: 多媒体与交互（2 周）
- [ ] Live2D 窗口集成（`live2d-py` + `QOpenGLWidget`）
- [ ] TTS Provider（Edge TTS）+ 音频播放队列
- [ ] 口型同步（TTS 事件驱动）
- [ ] Live Canvas 基础实现（图片/卡片弹窗）

### Phase 4: 技能系统（2 周）
- [ ] Skill Engine（SKILL.md 解析、Tool Schema 生成）
- [ ] 内置 Skills（天气、计算器、截图、浏览器控制）
- [ ] 工具调用循环（Tool Call -> Execute -> Resume）

### Phase 5: 打磨与分发（2 周）
- [ ] ASR 语音输入（可选）
- [ ] Launcher（Gateway 守护、单 exe 启动）
- [ ] PyInstaller 打包
- [ ] 文档、测试、Bug 修复

**总估算**: 约 **10 周**（2.5 个月）完成 v2.0 MVP。

---

## 11. 待讨论与待决策事项

以下问题需要在团队评审会议上确认：

1. **Gateway 架构是否被接受？** 是否有足够开发周期？
2. **Skill 系统形态**：采用 `SKILL.md` 文档驱动，还是回归传统 Python 插件接口？
3. **Live Canvas 优先级**：MVP 阶段是否必须实现，还是可以延后？
4. **远程客户端需求**：未来是否真的有 Web/手机端需求？这会影响协议设计的复杂度。
5. **AI 提供商默认选择**：继续默认 Gemini，还是支持多模型自由切换？
6. **ASR 是否为 MVP 必须项？**
7. **数据隐私级别**：是否需要将 Gateway 的某些操作放入沙箱容器（Docker）？
8. **开源策略**：v2.0 是否开源？如果开源，许可证选择（MIT / AGPL / 商业双许可）？

---

## 12. 附录

### 附录 A: 旧项目核心问题清单（供参考）

详见本文档第 1.2 节。旧项目的问题不是"需要优化"，而是"需要替换"。

### 附录 B: 参考项目

- **OpenClaw** (`https://github.com/openclaw/openclaw`): Gateway + Client 架构、Markdown 驱动 Skill、A2UI Canvas。
- **MCP (Model Context Protocol)** (`https://modelcontextprotocol.io`): 工具调用的标准化思路。
- **live2d-py** (`https://github.com/GuCatzai/live2d-py`): Python Live2D 渲染库。

### 附录 C: 术语表

| 术语 | 说明 |
|------|------|
| Gateway | AIPet 的后台核心服务，管理 AI、Session、Skill 等 |
| Client | Gateway 的前端客户端，如 PyQt Live2D 宠物、WebChat |
| Session | 一个隔离的 AI 对话上下文 |
| Skill | 一个可插拔的工具包，包含文档和 Python 实现 |
| Live Canvas | AI 主动推送的浮动可视化面板 |
| Soul | 角色的基础人格定义（Markdown） |
| Memory | AI 从对话中总结的持久化记忆（Markdown） |

---

**文档结束**
