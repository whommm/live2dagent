"""Tests for dynamic path resolution."""

from pathlib import Path

from aipet.utils.paths import ensure_directories, get_project_root, get_user_data_dir


def test_project_root_contains_pyproject_toml() -> None:
    root = get_project_root()
    assert (root / "pyproject.toml").exists()


def test_user_data_dir_is_not_hardcoded() -> None:
    path = get_user_data_dir()
    # Must be a Path object, not a string with hardcoded absolute Windows path
    assert isinstance(path, Path)
    assert "E:\\live2d" not in str(path)


def test_ensure_directories_creates_all_paths() -> None:
    dirs = ensure_directories()
    required = {"data", "config", "sessions", "skills", "models", "cache", "logs"}
    assert required.issubset(dirs.keys())
    for p in dirs.values():
        assert p.exists()
