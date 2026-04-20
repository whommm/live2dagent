# AIPet v2.0

> 🐾 **Your personal AI desktop companion with Live2D.**

AIPet v2.0 is a complete rewrite of the original AIPet project, built with a modern **Gateway + Client** architecture inspired by [OpenClaw](https://github.com/openclaw/openclaw).

## ✨ Key Features

- **Gateway Architecture**: The AI brain runs as a local WebSocket service. The PyQt Live2D frontend is just one of many possible clients.
- **Live2D Desktop Pet**: Transparent, borderless, always-on-top character with lip-sync and interactive expressions.
- **Markdown-Driven Skills**: Add new capabilities by dropping a `SKILL.md` file into the skills folder.
- **Persistent Memory**: Long-term memory stored in `memory.md`, short-term in SQLite.
- **Modular Providers**: Swap AI models, TTS engines, and ASR backends without touching core code.
- **Live Canvas**: AI can push rich visual cards, images, lists, code snippets and widgets next to your pet.
- **Task Scheduler**: Periodic scheduled execution of any skill tool, with persistence across restarts.
- **Skill Writer**: AI can autonomously write, test and install new skills.

## 🏗️ Architecture

```
[PyQt Live2D Client] <--ws--> [AIPet Gateway] <---> [AI/TTS/ASR Providers]
         ^
         |
    [WebChat / Mobile]
```

See [AIPet_v2_Architecture_Design.md](AIPet_v2_Architecture_Design.md) for the full design document.

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended for dependency management)

### Installation

```bash
# Clone the repository
git clone https://github.com/whommm/live2dagent.git
cd live2dagent

# Create virtual environment and install dependencies
uv sync --all-extras
```

### Configuration

1. Copy `config/providers.example.toml` to `config/providers.toml` and add your API Key.
2. Create `config/soul.md` to define your AI character personality.
3. Create `config/memory.md` as the AI's long-term memory notebook.

### Run

**Windows (double-click to run):**

- `start_all.bat` — Launch Gateway + Frontend
- `start_gateway.bat` — Launch Gateway only
- `start_frontend.bat` — Launch Frontend only

**Command line:**

```bash
# Terminal 1: Gateway
.venv\Scripts\python.exe -m aipet gateway

# Terminal 2: PyQt Frontend
.venv\Scripts\python.exe -m aipet frontend
```

## 📁 Project Structure

```
live2dagent/
├── config/              # Personal configuration (not committed)
│   ├── providers.toml   # AI provider config (API keys)
│   ├── gateway.toml     # Gateway runtime config
│   ├── soul.md          # Character personality / system prompt
│   └── memory.md        # Long-term memory (AI auto-maintained)
├── data/                # Runtime data (local generated)
│   ├── sessions/        # SQLite chat history
│   ├── skills/          # User skills directory
│   ├── cache/tts/       # TTS audio cache
│   └── tasks.json       # Scheduled tasks persistence
├── live2dmodels/        # Live2D model files (not committed)
├── src/aipet/           # Main source code
│   ├── gateway/         # Gateway core (asyncio + WebSocket)
│   ├── frontend/        # PySide6 Live2D client
│   └── utils/           # Shared utilities
├── tests/               # Test suite
├── docs/                # Documentation
├── pyproject.toml       # Project configuration
├── uv.lock              # Dependency lock
├── start_all.bat        # One-click launch
├── start_gateway.bat    # Gateway only
└── start_frontend.bat   # Frontend only
```

## 🛠️ Development

```bash
# Run tests
uv run pytest

# Run linting
uv run ruff check src tests
uv run ruff format src tests

# Run type checking
uv run mypy src
```

## 📜 License

MIT License - see [LICENSE](LICENSE) for details.
