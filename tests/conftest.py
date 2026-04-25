"""Global pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def isolate_aipet_dirs(monkeypatch, tmp_path):
    """Redirect all AIPet config and data directories to a temporary path during tests."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    import aipet.gateway.providers.manager
    import aipet.gateway.repository
    import aipet.utils.paths

    monkeypatch.setattr(aipet.utils.paths, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(aipet.utils.paths, "get_user_data_dir", lambda: data_dir)
    monkeypatch.setattr(aipet.gateway.providers.manager, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(aipet.gateway.repository, "get_user_data_dir", lambda: data_dir)
