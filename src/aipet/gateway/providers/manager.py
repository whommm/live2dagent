"""Provider management: Cherry Studio-style multi-provider configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import toml
from pydantic import BaseModel, Field

from aipet.gateway.providers.ai import AIProvider
from aipet.gateway.providers.ai_anthropic import AnthropicProvider
from aipet.gateway.providers.ai_echo import EchoProvider
from aipet.gateway.providers.ai_gemini import GeminiProvider
from aipet.gateway.providers.ai_ollama import OllamaProvider
from aipet.gateway.providers.ai_openai import OpenAIProvider
from aipet.utils.paths import get_config_dir

_logger = logging.getLogger("aipet.gateway.providers.manager")


class ProviderEntry(BaseModel):
    """A configured AI provider entry (like Cherry Studio's provider card)."""

    id: str
    name: str
    type: str  # "openai", "gemini", "anthropic", "ollama", "echo"
    api_key: str | None = None
    base_url: str | None = None  # Critical for OpenAI-compatible custom endpoints
    models: list[str] = Field(default_factory=list)
    is_custom: bool = False  # True for user-added OpenAI-compatible providers
    extra_params: dict[str, Any] = Field(default_factory=dict)  # e.g. thinking, reasoning_effort


class ProviderManager:
    """Manages a list of AI provider configurations and instantiates providers on demand."""

    CONFIG_FILE = "providers.toml"

    def __init__(self) -> None:
        self.providers: list[ProviderEntry] = []
        self.current_provider_id: str | None = None
        self.current_model: str | None = None
        self._load_or_init()

    def _config_path(self) -> Path:
        return get_config_dir() / self.CONFIG_FILE

    def _load_or_init(self) -> None:
        path = self._config_path()
        if path.exists():
            try:
                data = toml.load(path)
                raw_providers = data.get("providers", [])
                self.providers = [ProviderEntry.model_validate(p) for p in raw_providers]
                self.current_provider_id = data.get("current_provider_id")
                self.current_model = data.get("current_model")
                if self.current_provider_id is None and self.providers:
                    self.current_provider_id = self.providers[0].id
                    first = self.providers[0]
                    self.current_model = first.models[0] if first.models else None
                    self.save()
            except Exception:
                _logger.exception("Failed to load config")
                self._init_defaults()
        else:
            self._init_defaults()

    def _init_defaults(self) -> None:
        """Seed with built-in providers."""
        self.providers = [
            ProviderEntry(
                id="echo",
                name="Echo (Local Test)",
                type="echo",
                models=["echo"],
            ),
            ProviderEntry(
                id="gemini",
                name="Google Gemini",
                type="gemini",
                models=["gemini-2.5-flash-preview-05-20", "gemini-2.5-pro-preview-05-06"],
            ),
            ProviderEntry(
                id="openai",
                name="OpenAI",
                type="openai",
                base_url="https://api.openai.com/v1",
                models=["gpt-4o", "gpt-4o-mini", "o3-mini"],
            ),
            ProviderEntry(
                id="anthropic",
                name="Anthropic Claude",
                type="anthropic",
                models=["claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
            ),
            ProviderEntry(
                id="ollama",
                name="Ollama (Local)",
                type="ollama",
                base_url="http://localhost:11434",
                models=["llama3", "qwen2.5", "phi4"],
            ),
        ]
        self.current_provider_id = "echo"
        self.current_model = "echo"
        self.save()

    def save(self) -> None:
        """Persist provider list and current selection."""
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "providers": [p.model_dump(mode="json") for p in self.providers],
            "current_provider_id": self.current_provider_id,
            "current_model": self.current_model,
        }
        with open(path, "w", encoding="utf-8") as f:
            toml.dump(data, f)

    def list_providers(self) -> list[ProviderEntry]:
        return self.providers

    def get_provider(self, provider_id: str | None = None) -> ProviderEntry | None:
        pid = provider_id or self.current_provider_id
        for p in self.providers:
            if p.id == pid:
                return p
        return None

    def set_current(self, provider_id: str, model: str | None = None) -> bool:
        provider = self.get_provider(provider_id)
        if provider is None:
            return False
        if model and model not in provider.models:
            return False
        self.current_provider_id = provider_id
        if model:
            self.current_model = model
        elif provider.models:
            self.current_model = provider.models[0]
        else:
            self.current_model = None
        self.save()
        return True

    def add_provider(self, entry: ProviderEntry) -> bool:
        """Add a custom provider (e.g., DeepSeek, SiliconFlow)."""
        if any(p.id == entry.id for p in self.providers):
            return False
        self.providers.append(entry)
        self.save()
        return True

    def remove_provider(self, provider_id: str) -> bool:
        """Remove a provider. Built-ins can also be removed."""
        original_len = len(self.providers)
        self.providers = [p for p in self.providers if p.id != provider_id]
        if len(self.providers) < original_len:
            if self.current_provider_id == provider_id:
                # Fallback to first available
                if self.providers:
                    self.set_current(self.providers[0].id)
                else:
                    self.current_provider_id = None
                    self.current_model = None
            self.save()
            return True
        return False

    def update_provider(self, provider_id: str, **kwargs: Any) -> bool:
        """Update provider fields."""
        provider = self.get_provider(provider_id)
        if provider is None:
            return False
        updated = provider.model_copy(update=kwargs)
        for i, p in enumerate(self.providers):
            if p.id == provider_id:
                self.providers[i] = updated
                break
        self.save()
        return True

    def create_ai_provider(
        self, provider_id: str | None = None, model: str | None = None
    ) -> AIProvider:
        """Factory: create a runnable AI provider instance."""
        entry = self.get_provider(provider_id)
        if entry is None:
            _logger.warning("Provider not found, falling back to Echo", provider_id=provider_id)
            return EchoProvider()

        requested_pid = provider_id or self.current_provider_id
        is_current = requested_pid == self.current_provider_id

        if model:
            mid = model
        elif is_current:
            mid = self.current_model or (entry.models[0] if entry.models else "echo")
        else:
            mid = entry.models[0] if entry.models else "echo"

        if entry.type == "echo":
            return EchoProvider(model_id=mid)
        elif entry.type == "gemini":
            if not entry.api_key:
                _logger.warning(
                    "Gemini provider has no api_key, falling back to Echo", entry_id=entry.id
                )
                return EchoProvider(model_id=mid)
            return GeminiProvider(api_key=entry.api_key, model_id=mid)
        elif entry.type == "openai":
            if not entry.api_key:
                _logger.warning(
                    "OpenAI provider has no api_key, falling back to Echo", entry_id=entry.id
                )
                return EchoProvider(model_id=mid)
            return OpenAIProvider(
                api_key=entry.api_key,
                model_id=mid,
                base_url=entry.base_url or "https://api.openai.com/v1",
                extra_params=entry.extra_params,
            )
        elif entry.type == "anthropic":
            if not entry.api_key:
                _logger.warning(
                    "Anthropic provider has no api_key, falling back to Echo",
                    entry_id=entry.id,
                )
                return EchoProvider(model_id=mid)
            return AnthropicProvider(
                api_key=entry.api_key,
                model_id=mid,
                base_url=entry.base_url,
            )
        elif entry.type == "ollama":
            return OllamaProvider(
                model_id=mid,
                base_url=entry.base_url or "http://localhost:11434",
            )
        else:
            return EchoProvider(model_id=mid)
