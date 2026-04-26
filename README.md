# live2dagent

> 🐾 **你的个人 AI 桌面伴侣 —— 基于 Live2D 的透明桌宠 + Gateway 智能中枢。**

live2dagent 是一个完整的 AI 桌面宠物系统，采用 **Gateway + Client** 架构。Gateway 作为本地 WebSocket 智能中枢运行 AI 推理、工具调用和会话管理；Frontend 是一个基于 PySide6 + OpenGL 的透明 Live2D 桌面宠物，支持唇形同步、鼠标追踪、自动表演和丰富的可视化交互。

## ✨ 核心功能

### 🤖 Gateway 智能中枢

- **WebSocket 服务** — 本地异步网关，提供 20+ 协议方法（聊天、会话、画布、Provider、Live2D、TTS 等）
- **多 AI Provider 管理** — Cherry Studio 风格配置，支持 OpenAI、Google Gemini、Anthropic Claude、Ollama（本地）、Echo（调试）
- **多模型角色分配** — 意图识别、工具规划、总结、主动聊天可独立指定 Provider + Model
- **Phase-1 Decision 工具策略** — 轻量级意图判断 + 关键词召回，仅将相关工具送入上下文，显著降低 Token 消耗
- **Streaming Guard** — 状态机防止工具调用 JSON 泄漏到聊天流
- **会话持久化** — SQLite 存储聊天记录，支持多会话、自动重命名、批量加载
- **TTS 语音合成** — Microsoft Edge TTS，支持文件缓存（MD5）、自动清理（500MB 上限）、唇形同步数据生成
- **ASR 预留接口** — 配置支持 faster-whisper，Provider 层待实现
- **任务调度器** — 内置定时任务调度，支持周期性执行技能工具
- **主动聊天** — AI 在空闲时主动发起对话（可配置间隔与 TTS）

### 🎨 Frontend Live2D 桌面宠物

- **透明无边框窗口** — 全屏覆盖主显示器，始终置顶，支持 Win32 鼠标穿透（`WS_EX_TRANSPARENT`）
- **OpenGL Live2D 渲染** — 基于 `live2d-py` v3，支持 `.model3.json` / `.vtube.json` / `.prprl2d.json`
- **鼠标 gaze 追踪** — 眼球与头部实时跟随鼠标光标移动
- **自动表演系统** — 每 1–3 秒随机触发 7 种微动作（瞥视、翅膀抖动、歪头、微笑、叹气、挑眉、环顾）
- **丰富的表情与姿态** — 13 种情绪（开心、害羞、生气、委屈等）、12 种语义姿态、多种道具（翅膀、光环、祈祷、麦克风等），含互斥逻辑
- **边缘吸附** — 拖拽释放后自动吸附屏幕边缘，保持可见边距
- **HitPart 交互** — 点击模型不同部位触发不同反应
- **系统托盘** — 自定义爪印图标菜单，支持模型切换、勿扰模式、TTS 开关、显示/隐藏
- **状态报告** — 每 3 秒向 Gateway 发送当前姿态/情绪/道具的中文描述，注入 AI System Prompt
- **退出保护** — 毛玻璃风格 "Saving memories..." 遮罩，600ms 延迟后安全退出

### 💬 聊天与交互

- **Markdown 渲染** — `markdown-it-py` + Pygments 语法高亮，代码块带语言标识
- **文件拖拽** — 直接拖入文件作为附件，AI 可读取文件内容
- **会话管理** — 左侧边栏新建/重命名/删除会话，批量加载（15 条/批）保持流畅
- **消息气泡操作** — 复制（带 ✓ 反馈）、删除、重新生成、编辑重发
- **工具调用可视化** — 展开式卡片显示工具参数、执行结果、错误信息和耗时
- **模型快速切换** — 顶部下拉栏实时切换 Provider + Model
- **停止生成** — 生成中发送按钮变红，可随时停止 UI 流式输出
- **上箭头召回** — 空输入时按 ↑ 快速召回上一条用户消息
- **右侧边缘聊天触发条** — 屏幕右边缘 6px 彩色条，悬停展开，点击切换聊天窗口

### 🖼️ Live Canvas 浮动面板

AI 可通过技能在宠物旁边推送富视觉内容：

| 类型 | 说明 |
|---|---|
| `bubble` | 纯文本气泡，自动宽度 |
| `card` | Markdown 内容卡片 |
| `image` | 图片（支持 base64 / 本地路径）+ 标题 |
| `list` | 列表，支持勾选项 |
| `code` | 暗色代码块 |
| `rich` | 自定义 HTML |

- 多画布重叠时自动垂直堆叠避让
- 点击可触发动作（如打开聊天窗口）
- 悬停暂停自动淡出

### 🧩 Skills 技能系统

- **Markdown 驱动** — 每个技能是一个目录，包含 `SKILL.md`（描述）和 `__init__.py`（工具实现）
- **11 个内置技能** — 计算器、时间、随机数、天气、文件操作、记忆、Shell、Canvas、调度器、Skill Writer、Skill Tester
- **Skill Writer** — AI 可自主编写、测试、安装新技能
- **权限沙箱** — `network` / `filesystem` / `process` 三级权限控制，未授权操作被拦截
- **代码安全扫描** — AST 静态分析禁止 `os` / `subprocess` / `sys` / `eval` / `exec` 等危险调用
- **Phase-1 Decision** — 智能判断是否需要调用工具，减少无效上下文

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│                      PySide6 Frontend                        │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐  │
│  │ Live2D 宠物  │   │  聊天窗口   │   │ Live Canvas 面板 │  │
│  │ (OpenGL)    │   │ (Markdown)  │   │ (浮动气泡/卡片)   │  │
│  └──────┬──────┘   └──────┬──────┘   └────────┬─────────┘  │
│         │                 │                    │             │
│         └─────────────────┴────────────────────┘             │
│                           │                                  │
│                    WebSocket Client                          │
│                           │                                  │
└───────────────────────────┼──────────────────────────────────┘
                            │ ws://127.0.0.1:18790
┌───────────────────────────┼──────────────────────────────────┐
│                    Gateway Server (asyncio)                  │
│                           │                                  │
│  ┌──────────┐  ┌─────────┴──────────┐  ┌─────────────────┐ │
│  │ Session  │  │   AI Providers     │  │   Skills        │ │
│  │ Manager  │  │ (OpenAI/Gemini/… ) │  │ (Tool Router)   │ │
│  └──────────┘  └────────────────────┘  └─────────────────┘ │
│  ┌──────────┐  ┌────────────────────┐  ┌─────────────────┐ │
│  │  SQLite  │  │   TTS / Audio      │  │   Scheduler     │ │
│  │  Store   │  │   (Edge TTS)       │  │   (定时任务)     │ │
│  └──────────┘  └────────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（推荐，用于依赖管理）
- Windows（Frontend 依赖 Win32 API 实现鼠标穿透和边缘吸附）

### 安装

```bash
# 克隆仓库
git clone https://github.com/whommm/live2dagent.git
cd live2dagent

# 创建虚拟环境并安装全部依赖
uv sync --all-extras
```

### 配置

1. **复制 Provider 示例并填入你的 API Key**
   ```bash
   copy config\providers.example.toml config\providers.toml
   ```
   编辑 `config/providers.toml`，填入你的 API Key、Base URL 和模型列表。

2. **创建角色人格文件**
   ```bash
   echo. > config\soul.md
   ```
   在 `config/soul.md` 中定义你的 AI 角色性格、说话风格和 Live2D 控制规则。（可参考内置默认设定或自行编写。）

3. **创建长期记忆文件**
   ```bash
   echo. > config\memory.md
   ```
   `config/memory.md` 由 AI 自动维护，记录主人的喜好和故事。

4. **（可选）调整 Gateway 运行时配置**
   ```bash
   echo. > config\gateway.toml
   ```
   `config/gateway.toml` 支持 20+ 项配置：端口、工具策略、多模型角色、主动聊天参数等。首次启动时 Gateway 会使用默认值，也可随时手动创建此文件覆盖配置。

### 运行

**推荐方式 — 一键启动（自动启动 Gateway + Frontend）：**

```bash
# 命令行
.venv\Scripts\python.exe -m aipet

# 或双击运行
start_all.bat
```

**分别启动（开发调试）：**

```bash
# 终端 1: Gateway
.venv\Scripts\python.exe -m aipet gateway

# 终端 2: Frontend
.venv\Scripts\python.exe -m aipet frontend
```

**BAT 快捷脚本：**

| 脚本 | 作用 |
|---|---|
| `start_all.bat` | 启动 Gateway + Frontend |
| `start_gateway.bat` | 仅启动 Gateway |
| `start_frontend.bat` | 仅启动 Frontend |

> BAT 脚本会自动检测 `.venv\Scripts\python.exe`，无需手动激活虚拟环境。

---

## ⚙️ 配置详解

### `config/providers.toml` — AI Provider 配置

支持多 Provider 同时配置，运行时随时切换。

```toml
current_provider_id = "openai"
current_model = "gpt-4o"

[[providers]]
id = "openai"
name = "OpenAI"
type = "openai"
api_key = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"
base_url = "https://api.openai.com/v1"
models = ["gpt-4o", "gpt-4o-mini"]
is_custom = true

[[providers]]
id = "ds"
name = "DeepSeek"
type = "openai"
api_key = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"
base_url = "https://api.deepseek.com/v1"
models = ["deepseek-chat", "deepseek-reasoner"]
is_custom = true
```

支持的 `type`：`openai`（兼容 OpenAI 的 API，如 DeepSeek、SiliconFlow）、`gemini`、`anthropic`、`ollama`、`echo`。

### `config/gateway.toml` — Gateway 运行时配置

```toml
gateway_bind = "127.0.0.1"
gateway_port = 18790

# AI 默认 Provider（当 providers.toml 中无匹配时的兜底）
ai_provider = "echo"
ai_model = "gemini-2.5-flash-preview-05-20"

# TTS
tts_provider = "edge-tts"
tts_default_voice = "zh-CN-XiaoxiaoNeural"
tts_auto_play = false

# 主动聊天
proactive_enabled = true
proactive_interval_min = 60
proactive_interval_max = 180
proactive_tts = true

# 工具策略
tool_calling_strategy = "phase1_decision"   # phase1_decision | legacy
tool_context_mode = "direct_schema"         # direct_schema | brief_schema
phase1_max_candidate_tools = 8
phase1_direct_confidence_threshold = 0.55
max_tool_loops = 5
enable_streaming_guard = true

# 多模型角色（可选，留空则跟随主 Provider）
# intent_provider_id = "ds"
# intent_model = "deepseek-chat"
# tool_provider_id = "ds"
# tool_model = "deepseek-chat"
```

> 所有配置项均支持通过 `AIPET_*` 环境变量覆盖，例如 `AIPET_GATEWAY_PORT=18800`。

### `config/soul.md` — 角色人格

AI 的 System Prompt。你可以定义：
- 角色基础设定（外貌、身份）
- 性格与说话风格
- Live2D 控制规则（`[pose:xxx]`、`[emotion:xxx]`、`[prop:xxx]`）
- 桌宠行为规则（主动问候、互动请求、回复长度）
- 程序员人格 / 全能助手模式
- 自我进化意识

### `config/memory.md` — 长期记忆

由 AI 自主维护的 Markdown 文件。当对话中出现值得记住的信息时，AI 会通过 `memory:update_memory` 工具更新此文件，确保下次聊天还记得。

### `config/ai_tools.toml` — AI 工具运行时配置

通过 Frontend 的「AI & 工具设置」对话框动态调整，包含：
- TTS 自动播放、主动聊天开关
- 工具策略与阈值
- 各角色（意图/工具/总结/主动）使用的 Provider 和 Model

---

## 🧩 技能系统

### 内置技能（11 个）

| 技能 | 权限 | 说明 |
|---|---|---|
| `calculator` | — | 安全 AST 数学计算 |
| `time` | — | 获取当前时间/日期/城市时间 |
| `random` | — | 随机数、随机选择、抛硬币、掷骰子 |
| `weather` | `network` | 查询天气（via wttr.in） |
| `file` | `filesystem` | 读/写/编辑文件、列出目录 |
| `memory` | `filesystem` | 读取/更新 `memory.md` |
| `shell` | `filesystem`, `network`, `process` | 执行 Shell 命令（限制元字符） |
| `canvas` | `network` | 在宠物旁显示 bubble/card/image/list/code/rich |
| `scheduler` | — | 创建/取消/删除/列出定时任务 |
| `skill_writer` | `filesystem` | AI 自主编写、安装新技能 |
| `skill_tester` | `filesystem` | 对草稿技能进行 lint + 冒烟测试 |

### 自定义技能

在 `data/skills/` 下创建目录，放入 `SKILL.md` 和 `__init__.py`：

```
data/skills/my_skill/
├── SKILL.md          # 技能描述（支持 YAML frontmatter）
└── __init__.py       # 必须暴露 tools = {"tool_name": function}
```

重启 Gateway 或调用 `skill_writer:install_skill` 即可自动加载。

### Skill Writer

直接告诉琉璃（你的桌宠）："帮我写一个新技能，用来查询每日油价"，AI 会：
1. 生成符合规范的 `SKILL.md` 和 `__init__.py`
2. 自动运行 lint 和冒烟测试
3. 安装到 `data/skills/`
4. 立即可用

---

## 📁 项目结构

```
live2dagent/
├── config/                     # 个人配置（.gitignore 忽略）
│   ├── providers.toml          # AI Provider 配置（API Key）
│   ├── gateway.toml            # Gateway 运行时配置
│   ├── ai_tools.toml           # AI 工具运行时配置
│   ├── soul.md                 # 角色人格 / System Prompt
│   └── memory.md               # 长期记忆（AI 自动维护）
├── data/                       # 运行时数据（.gitignore 忽略）
│   ├── sessions/               # SQLite 聊天记录
│   ├── skills/                 # 用户自定义技能
│   ├── cache/tts/              # TTS 音频缓存
│   └── tasks.json              # 定时任务持久化
├── live2dmodels/               # Live2D 模型文件（.gitignore 忽略）
│   └── PurpleBird/             # 默认模型示例
├── builtin_skills/             # 内置技能（随项目分发）
│   ├── calculator/
│   ├── canvas/
│   ├── file/
│   ├── memory/
│   ├── random/
│   ├── scheduler/
│   ├── shell/
│   ├── skill_tester/
│   ├── skill_writer/
│   ├── time/
│   └── weather/
├── src/aipet/                  # 主源码
│   ├── launcher.py             # 生产启动器（Gateway + Frontend）
│   ├── __main__.py             # CLI 入口 `python -m aipet`
│   ├── gateway/                # Gateway 核心
│   │   ├── server.py           # WebSocket 服务主入口
│   │   ├── config.py           # 配置模型（pydantic-settings）
│   │   ├── session.py          # 会话管理
│   │   ├── protocol.py         # 通信协议
│   │   ├── events.py           # 事件总线
│   │   ├── state.py            # 应用状态存储
│   │   ├── streaming.py        # 流式响应与 Tool Guard
│   │   ├── tool_decision.py    # Phase-1 Decision
│   │   ├── scheduler.py        # 定时任务调度器
│   │   ├── canvas.py           # Live Canvas 管理
│   │   ├── models.py           # 数据模型
│   │   ├── repository.py       # SQLite 持久化
│   │   ├── providers/          # Provider 层
│   │   │   ├── ai.py           # AI Provider 协议
│   │   │   ├── ai_openai.py    # OpenAI 兼容（含 DeepSeek）
│   │   │   ├── ai_gemini.py    # Google Gemini
│   │   │   ├── ai_anthropic.py # Anthropic Claude
│   │   │   ├── ai_ollama.py    # Ollama 本地推理
│   │   │   ├── ai_echo.py      # Echo 调试
│   │   │   ├── factory.py      # Provider 工厂
│   │   │   ├── manager.py      # 多 Provider 管理器
│   │   │   ├── tts.py          # TTS 协议
│   │   │   └── tts_edge.py     # Edge TTS 实现
│   │   ├── media/              # 媒体处理
│   │   │   ├── audio_player.py # 异步音频播放队列
│   │   │   └── lipsync.py      # RMS 唇形同步数据分析
│   │   └── skills/             # 技能系统
│   │       ├── registry.py     # 技能注册与发现
│   │       ├── router.py       # 工具路由执行
│   │       ├── sandbox.py      # 权限沙箱
│   │       ├── schema.py       # 动态函数 Schema 生成
│   │       └── writer.py       # AI Skill Writer
│   ├── frontend/               # PySide6 Live2D 客户端
│   │   ├── app.py              # Frontend 入口
│   │   ├── pet_window.py       # 桌面宠物主窗口（透明/置顶/托盘）
│   │   ├── live2d_widget.py    # Live2D OpenGL 渲染
│   │   ├── chat_window.py      # 聊天 UI（Markdown/会话管理）
│   │   ├── chat_trigger.py     # 屏幕右边缘聊天触发条
│   │   ├── live_canvas.py      # Live Canvas 浮动面板
│   │   ├── client.py           # WebSocket 客户端
│   │   ├── provider_dialog.py  # Provider 管理对话框
│   │   ├── ai_tool_settings_dialog.py  # AI & 工具高级设置
│   │   └── theme.py            # Material Design 3 主题
│   └── utils/                  # 工具模块
│       ├── log.py              # 结构化日志
│       └── paths.py            # 路径管理
├── tests/                      # 测试套件
├── docs/                       # 文档
├── pyproject.toml              # 项目配置与依赖
├── uv.lock                     # 依赖锁定
├── start_all.bat               # 一键启动
├── start_gateway.bat           # 仅启动 Gateway
├── start_frontend.bat          # 仅启动 Frontend

```

---

## 🛠️ 开发

```bash
# 运行测试
uv run pytest

# 代码检查与格式化
uv run ruff check src tests
uv run ruff format src tests

# 类型检查
uv run mypy src
```

### 添加新 Provider

1. 在 `src/aipet/gateway/providers/` 下新建 `ai_xxx.py`
2. 实现 `AIProvider` 协议（`chat()` 和 `compact()`）
3. 在 `factory.py` 和 `manager.py` 中注册
4. 在 `provider_dialog.py` 的 UI 中添加类型选项

### 添加新技能

1. 在 `data/skills/{skill_name}/` 创建目录
2. 编写 `SKILL.md`（描述 + 权限声明）
3. 编写 `__init__.py`（`tools = {...}`）
4. 重启 Gateway 自动加载

---

## 📜 License

MIT License — 详见 [LICENSE](LICENSE)。
