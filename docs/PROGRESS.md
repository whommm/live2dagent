# AIPet v2.0 开发进度日志

> **最后更新**: 2026-04-14  
> **当前状态**: 核心骨架已完成，Gateway + Frontend 可运行，甘雨模型已加载

---

## 🎉 已完成功能

### 架构层
- [x] **Gateway + Client 架构**: 后台 WebSocket 服务 (`ws://127.0.0.1:18790`) + PyQt 前端
- [x] **类型安全 EventBus**: 替代 `pyqtSignal`，支持异步并发分发
- [x] **State Store**: 单一不可变状态源，支持订阅和快照
- [x] **动态路径管理**: 零硬编码绝对路径，自动适配 `%APPDATA%/AIPet`
- [x] **生产启动器 (Launcher)**: 自动检测并拉起 Gateway 进程

### 数据层
- [x] **SQLite 持久化**: 每个 Session 独立 `chat.db`，存储聊天记录和 Session 元数据
- [x] **Session 管理器**: 支持创建、列出、加载持久化 Session，自动上下文组装
- [x] **Prompt 组装**: 自动读取 `soul.md` + `memory.md` 注入 AI 系统提示

### AI 层
- [x] **Cherry Studio 风格提供商管理**: `providers.toml` 持久化，支持多 Provider
- [x] **内置 Provider**: Echo (测试)、Gemini、OpenAI、Anthropic、Ollama
- [x] **自定义 OpenAI 兼容 API**: 支持 `base_url`，可用于 DeepSeek、硅基流动等
- [x] **运行时模型切换**: Chat Window 顶部下拉框可直接切换模型
- [x] **流式聊天**: `chat.stream` 协议支持，前端可逐字显示

### UI 层
- [x] **Chat Window**: 气泡式聊天、Enter 发送/Shift+Enter 换行、流式更新、输入指示器
- [x] **Live2D 宠物窗口**: 无边框、透明背景、始终置顶
- [x] **鼠标拖拽**: 按住甘雨任意位置可拖动窗口
- [x] **右键菜单**: 甘雨身上右键可弹出菜单（Open Chat / Show-Hide / Quit）
- [x] **系统托盘**: 托盘常驻，支持双击显示/隐藏

### 集成
- [x] **模型文件部署**: `ganyu`、`linghu`、`yueliang` 已复制到 `%APPDATA%/AIPet/models/`
- [x] **live2d-py 集成**: OpenGL 渲染正常，表情/动作控制接口可用
- [x] **24 个单元测试通过**: 覆盖 EventBus、State、Gateway 协议、Provider、Repository、前端组件

---

## 📋 剩余工作清单（按优先级）

### 🔴 P0 - 核心体验（建议明天优先）

1. **聊天历史加载**
   - **现状**: Chat Window 打开是空白，只能看到新发的消息
   - **目标**: 打开聊天窗时自动请求 `chat.history`，从 SQLite 拉取并渲染成气泡
   - **涉及文件**: `gateway/server.py`, `gateway/repository.py`, `frontend/chat_window.py`
   - **预估工时**: 1-2 小时

2. **TTS 语音合成 + 音频播放**
   - **现状**: 只写了 Provider 抽象，没有实际声音
   - **目标**: 
     - 集成 `edge-tts` 合成语音
     - 用 `sounddevice`/`soundfile` 播放音频队列
     - 播放时向 Frontend 发送 `tts.start` / `tts.end` 事件
     - Live2DWidget 订阅事件并做口型同步（`set_lipsync` 已经预留了钩子）
   - **涉及文件**: `gateway/providers/` (新建 TTS provider), `gateway/media/audio_player.py`, `frontend/live2d_widget.py`
   - **预估工时**: 3-4 小时

3. **Skill Engine（文档驱动插件）**
   - **现状**: 这是 v2 最具特色的设计，但还没实现
   - **目标**:
     - 扫描 `~/.aipet/skills/` 目录
     - 读取每个 Skill 的 `SKILL.md` 文档
     - 生成 OpenAI Function Calling 格式的 `tools` 列表
     - 当 AI 返回 `tool_calls` 时，路由到对应的 Python 函数执行
   - **涉及文件**: `gateway/skills/` (新建目录), `gateway/server.py`
   - **预估工时**: 4-6 小时

### 🟠 P1 - 功能完善

4. **ASR 语音输入**
   - 按住说话 -> `faster-whisper` 转文字 -> 自动发送

5. **Live Canvas（A2UI）**
   - AI 主动弹出浮动面板显示图片/天气卡片/代码块

6. **系统托盘图标 + 边缘吸附**
   - 托盘加一个图标 PNG；拖到屏幕边缘自动贴边

### 🟡 P2 - 工程化与分发

7. **提供商管理弹窗**
   - 比顶部下拉框更完整的 UI：添加/编辑/删除 Provider，输入 base_url 和 api_key

8. **PyInstaller 打包脚本**
   - 一键生成 `.exe`，包含 Gateway + Frontend + `Live2DCubismCore.dll`

9. **配置管理界面**
   - 在 UI 里直接修改 TOML，不用手动去 `%APPDATA%` 翻文件

---

## 🐛 已知问题

| 问题 | 严重程度 | 说明 |
|------|---------|------|
| 左键点击甘雨无表情反应 | 🟡 低 | `HitPart` 逻辑已修复为始终触发，但用户反馈仍无反应。可能是 `set_expression` 后模型没有立即重绘，或甘雨模型缺少 `happy` 表情。需调试 `available_expressions` 列表 |
| 系统托盘图标空白 | 🟡 低 | 未提供图标文件，需要放一张 PNG 到资源目录并设置 |
| `google-generativeai` 已弃用 | 🟡 低 | Gemini Provider 使用的是旧版 SDK，官方建议迁移到 `google.genai` |
| websockets 14+ 签名变更 | 🟢 已修复 | `handle_client` 已适配 |
| 缺少 `chat.history` 接口 | 🟠 中 | 影响历史消息加载，需明天补充 |

---

## 🚀 明天启动方式

### 启动开发环境
```powershell
cd E:
.\live2d\aipet-v2
```

### 启动 Gateway
```powershell
.\.venv\Scripts\python.exe -m aipet gateway
```

### 启动桌面宠物
```powershell
.\.venv\Scripts\python.exe -m aipet frontend
```

---

## 📝 明天的建议切入点

**推荐顺序**：
1. **先跑测试确认状态**: `.\.venv\Scripts\pytest -v` （应显示 24 passed）
2. **补 `chat.history` 接口**: 这是用户体验最痛的点，改完 Chat Window 就能"有记忆"
3. **根据精力选择**:
   - 精力充沛 → 继续做 **TTS + 口型同步**
   - 想稳一点 → 修复 **左键表情反馈 + 托盘图标**

---

## 💾 Git 提交历史（供追溯）

```
8362f88 Initial commit: project scaffold
67000c0 Step 1: Gateway WebSocket Protocol + Session Management
2d1898e Step 2: AI Provider Abstraction + Gemini Implementation
f68d7f7 Step 3: Session Manager + SQLite Chat History Persistence
3944b2e Step 4: Chat Window UI with Bubbles, Streaming
a5f7542 Step 5: Live2D Window Integration with System Tray
301fdf6 Fix Live2D model loading: copy models and add fallback paths
b279ab7 Fix Gateway crash on missing API key: default to Echo provider
7cc78d3 Fix Gateway connection crash and Live2D interaction issues
55b4881 Add Cherry Studio-style provider management with runtime model switching
```

---

## 📁 关键文件速查

| 功能 | 文件路径 |
|------|---------|
| Gateway 入口 | `src/aipet/gateway/server.py` |
| 协议定义 | `src/aipet/gateway/protocol.py` |
| Session/模型 | `src/aipet/gateway/models.py`, `session.py` |
| SQLite 持久化 | `src/aipet/gateway/repository.py` |
| Provider 管理 | `src/aipet/gateway/providers/manager.py` |
| Chat Window | `src/aipet/frontend/chat_window.py` |
| Live2D 渲染 | `src/aipet/frontend/live2d_widget.py` |
| 宠物窗口 | `src/aipet/frontend/pet_window.py` |
| 启动器 | `src/aipet/launcher.py` |

---

晚安，明天继续！🌙
