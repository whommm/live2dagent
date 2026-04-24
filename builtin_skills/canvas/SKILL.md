# Canvas Skill

## Description
Control the Live Canvas floating panel on the frontend. Use this to display rich visual content such as cards, images, lists, and code snippets next to the Live2D pet.

## Brief
当用户需要展示信息卡片、图片、列表、代码片段或关闭浮动面板时调用。

## Permissions
- network

## Tools

- `show_bubble(text: str, duration_ms: int = 6000)` -> dict
  - Show a simple text bubble above the pet's head.
  - `text`: The text content to display.
  - `duration_ms`: How long the bubble stays visible (0 = persist until closed).
  - Returns: `{"canvas_id": str, "status": "shown"}`

- `show_card(title: str, content: str, icon: str = "", theme: str = "purple", duration_ms: int = 0)` -> dict
  - Show a structured info card.
  - `title`: Card header title.
  - `content`: Main body text (supports markdown).
  - `icon`: Emoji icon for the card.
  - `theme`: Color theme - "purple", "blue", "green", "orange", "red".
  - `duration_ms`: Auto-close time (0 = persist).
  - Returns: `{"canvas_id": str, "status": "shown"}`

- `show_image(src: str, caption: str = "", width: int = 280, duration_ms: int = 0)` -> dict
  - Show an image panel.
  - `src`: Image URL or base64 data URI.
  - `caption`: Optional caption text.
  - `width`: Panel width in pixels.
  - `duration_ms`: Auto-close time (0 = persist).
  - Returns: `{"canvas_id": str, "status": "shown"}`

- `show_list(title: str, items: list[str], checkable: bool = False, duration_ms: int = 0)` -> dict
  - Show a list panel.
  - `title`: List title.
  - `items`: List of item strings.
  - `checkable`: Whether items show checkboxes.
  - `duration_ms`: Auto-close time (0 = persist).
  - Returns: `{"canvas_id": str, "status": "shown"}`

- `show_code(code: str, language: str = "python", title: str = "Code", duration_ms: int = 0)` -> dict
  - Show a code snippet panel with syntax highlighting.
  - `code`: The source code text.
  - `language`: Programming language for highlighting.
  - `title`: Panel title.
  - `duration_ms`: Auto-close time (0 = persist).
  - Returns: `{"canvas_id": str, "status": "shown"}`

- `close(canvas_id: str)` -> dict
  - Close a specific canvas by ID.
  - `canvas_id`: The canvas ID returned by a previous show_* call.
  - Returns: `{"status": "closed"}`

## Examples
User: "帮我显示一个待办列表"
→ 调用 `show_list("今日待办", ["写代码", "测试", "部署"])`

User: "展示这张图片"
→ 调用 `show_image("https://example.com/cat.png", caption=" Cute cat")`
