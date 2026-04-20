# AIPet 工具扩展路线图

> 状态：规划中 | 最后更新：2026-04-19

## 当前工具盘点

| 技能 | 工具 | 功能 |
|------|------|------|
| calculator | calculate | 数学表达式求值 |
| canvas | show_bubble / show_card / show_image / show_list / show_code / close | 桌面浮动面板展示 |
| random | random_number / random_choice / flip_coin / roll_dice | 随机数与决策 |
| time | get_current_time / get_current_date / get_time_in_city | 时间查询 |
| weather | get_weather | 天气查询 |

---

## 第一阶段：桌面交互核心（高优先级）

利用 AIPet **驻留桌面** 的独特优势，做其他 AI 做不到的事。

### 1. 剪贴板助手 `clipboard`

**价值**：使用频率极高，桌宠独有的便利。

**工具列表**：
- `clipboard:read` — 读取当前剪贴板内容
- `clipboard:write` — 写入内容到剪贴板

**典型对话**：
```
用户: 帮我把剪贴板里的英文翻译成中文贴回去
琉璃: [clipboard:read → 翻译 → clipboard:write]
「主人，已经翻译好贴回去啦～直接粘贴就行」
```

**技术要点**：
- Windows 用 `pyperclip` 或 `win32clipboard`
- 需要 `clipboard` 权限声明
- 读取内容超过 4096 字符时截断并提示

---

### 2. 截图识图 `screenshot`

**价值**：桌面宠物的核心竞争力。琉璃可以"看到"用户在做什么。

**工具列表**：
- `screenshot:capture` — 截取全屏或指定区域
- `screenshot:analyze` — 将截图发给 AI 分析（结合 vision model）

**典型对话**：
```
用户: 琉璃，帮我看看这个报错是什么意思
琉璃: [screenshot:capture → analyze]
「主人，这个是因为端口被占用了，关掉那个进程就好～」
```

**技术要点**：
- Windows 用 `PIL.ImageGrab`
- 支持全屏 / 主显示器 / 指定区域 (x, y, w, h)
- 截图临时存储在 `data/cache/screenshots/`
- 涉及隐私，**必须在 SKILL.md 中明确声明权限**

---

### 3. 提醒与闹钟 `reminder`

**价值**：培养用户依赖，到点主动唤醒。

**工具列表**：
- `reminder:set` — 设置提醒（支持相对时间如"30分钟后"、绝对时间如"14:00"）
- `reminder:list` — 列出未完成的提醒
- `reminder:cancel` — 取消指定提醒

**典型对话**：
```
用户: 30分钟后提醒我开会
琉璃: [reminder:set "开会", 30min]
「好的主人，30分钟后琉璃会叫你～」

（30分钟后）
琉璃: [主动弹出气泡] 「主人，开会时间到啦！不要迟到哦～[expression:xingxing]」
```

**技术要点**：
- 需要后台定时器线程（与 Gateway 主事件循环协作）
- 提醒持久化到 SQLite（重启后不丢失）
- 触发时通过 WebSocket 向前端发送 `reminder.trigger` 事件

---

## 第二阶段：信息增强（中优先级）

### 4. 联网搜索 `web_search`

**价值**：弥补模型知识截止日期。

**工具列表**：
- `web_search:search` — 关键词搜索，返回摘要
- `web_search:fetch_page` — 获取指定 URL 的正文内容

**典型对话**：
```
用户: 今天有什么科技新闻
琉璃: [web_search:search "2026-04-19 科技新闻"]
「主人，今天有这几个大新闻：1. ... 2. ... 要详细看哪条？」
```

**技术要点**：
- 搜索可用 DuckDuckGo API（免费，无需 key）
- 页面抓取注意防反爬、设置 User-Agent
- 返回结果做长度限制，避免 token 爆炸

---

### 5. 系统状态 `system`

**价值**：增加"真实存在感"。

**工具列表**：
- `system:get_cpu_usage` — CPU 使用率
- `system:get_memory_usage` — 内存使用率
- `system:get_battery` — 电池电量（笔记本）
- `system:get_uptime` — 系统运行时间

**典型对话**：
```
琉璃: 「主人，你的电脑CPU已经80度了，休息一下吧～」
```

**技术要点**：
- Windows 用 `psutil`
- 可结合 ProactiveChat 周期性检查，主动关心用户

---

### 6. 媒体控制 `media`

**价值**：陪主人听歌、切歌。

**工具列表**：
- `media:play_pause` — 播放/暂停
- `media:next` — 下一首
- `media:previous` — 上一首
- `media:set_volume` — 设置音量 (0-100)

**技术要点**：
- Windows 用 `pycaw` 控制音量
- 播放控制可用系统媒体键模拟（`keyboard` 库）或 Windows Media API

---

### 7. 快速笔记 `note`

**价值**：随手记录，养成使用习惯。

**工具列表**：
- `note:write` — 追加笔记到指定文件（默认 `data/notes/quick_notes.md`）
- `note:read` — 读取最近 N 条笔记
- `note:search` — 搜索笔记内容

---

## 第三阶段：进阶交互（低优先级）

### 8. 窗口管理 `window`

**工具列表**：
- `window:list` — 列出当前窗口标题
- `window:focus` — 聚焦到指定窗口
- `window:minimize` — 最小化指定窗口
- `window:close` — 关闭指定窗口

**技术要点**：
- Windows 用 `pygetwindow` 或 `pywin32`
- 涉及系统级操作，需声明 `window_management` 权限

---

### 9. 文件速览 `file`

**工具列表**：
- `file:list` — 列出指定目录文件
- `file:read` — 读取文本文件内容（限制大小）
- `file:open` — 用系统默认程序打开文件

---

### 10. 屏幕亮度与音量 `system_display`

**工具列表**：
- `system_display:set_brightness` — 设置屏幕亮度 (0-100)
- `system_display:set_volume` — 设置系统音量 (0-100)

---

## 实现优先级建议

```
Phase 1（1-2 周）
├── clipboard   ← 用户高频需求，实现简单
├── reminder    ← 需要定时器架构设计
└── screenshot  ← 涉及隐私声明，需谨慎

Phase 2（2-3 周）
├── web_search  ← 需要外部 API 调研
├── system      ← psutil 即可
├── media       ← Windows API 调研
└── note        ← 简单文件操作

Phase 3（按需）
├── window
├── file
└── system_display
```

---

## 通用技术规范

每个新技能必须包含：

1. **`SKILL.md`** — 描述、权限声明、使用示例
2. **`__init__.py`** — 导出 `tools = {...}`
3. **权限标签** — 如 `clipboard`、`screenshot`、`filesystem` 等
4. **输入校验** — 所有参数做边界检查
5. **错误处理** — 返回友好错误信息（不抛异常到前端）

### 权限声明示例

```markdown
## Permissions
- clipboard: 读取和修改系统剪贴板内容
- screenshot: 截取屏幕画面
- network: 访问互联网搜索信息
```

---

## 待决策事项

- [ ] 提醒功能的后端定时器用 `asyncio` 还是独立线程？
- [ ] 截图是否需要在 UI 上加一个"允许截图"的开关？
- [ ] 联网搜索用 DuckDuckGo（免费）还是接入用户的搜索 API？
- [ ] 是否需要让用户在设置面板里开关每个技能的启用状态？
