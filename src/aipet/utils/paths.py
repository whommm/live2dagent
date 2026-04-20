"""Path resolution utilities.

All paths in AIPet are resolved dynamically. There are NO hardcoded absolute paths.
"""

import sys
from pathlib import Path


def get_project_root() -> Path:
    """Return the project root directory (where pyproject.toml lives).

    In development, this is the repo root.
    In PyInstaller bundles, this is the _MEIPASS or executable directory.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller bundle
        return Path(sys.executable).parent
    # Development: go up from src/aipet/utils/paths.py to repo root
    return Path(__file__).resolve().parents[3]


def get_user_data_dir() -> Path:
    """Return the AIPet data directory inside the project root.

    All runtime data (sessions, skills, models, cache, logs) lives here
    so everything is self-contained within the project.
    """
    return get_project_root() / "data"


def get_config_dir() -> Path:
    """Return the AIPet config directory inside the project root.

    This keeps configuration files (gateway.toml, soul.md, memory.md, providers.toml)
    alongside the source code for easy editing and version control.
    """
    return get_project_root() / "config"


def ensure_directories() -> dict[str, Path]:
    """Create all necessary directories and return their paths."""
    data_dir = get_user_data_dir()
    config_dir = get_config_dir()
    dirs = {
        "data": data_dir,
        "config": config_dir,
        "sessions": data_dir / "sessions",
        "skills": data_dir / "skills",
        "models": data_dir / "models",
        "cache": data_dir / "cache",
        "logs": data_dir / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs
