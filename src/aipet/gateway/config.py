"""Gateway configuration using pydantic-settings and TOML."""

from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aipet.utils.paths import get_config_dir


class GatewayConfig(BaseSettings):
    """Gateway runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AIPET_",
        env_nested_delimiter="__",
        toml_file=get_config_dir() / "gateway.toml",
    )

    gateway_bind: str = Field(default="127.0.0.1")
    gateway_port: int = Field(default=18790)
    gateway_log_level: str = Field(default="INFO")

    ai_provider: Literal["gemini", "openai", "anthropic", "ollama", "echo"] = Field(default="echo")
    ai_model: str = Field(default="gemini-2.5-flash-preview-05-20")
    ai_api_key: str | None = Field(default=None)
    ai_max_context_tokens: int = Field(default=128000)

    tts_provider: Literal["edge-tts", "system"] = Field(default="edge-tts")
    tts_default_voice: str = Field(default="zh-CN-XiaoxiaoNeural")
    tts_auto_play: bool = Field(default=False)

    asr_enabled: bool = Field(default=False)
    asr_provider: Literal["faster-whisper"] = Field(default="faster-whisper")

    live2d_default_model: str = Field(default="PurpleBird")
    live2d_auto_show: bool = Field(default=True)

    skills_auto_load: bool = Field(default=True)
    skills_allowed: list[str] = Field(default_factory=lambda: ["*"])

    proactive_enabled: bool = Field(default=True)
    proactive_interval_min: int = Field(default=60)
    proactive_interval_max: int = Field(default=180)
    proactive_tts: bool = Field(default=True)

    tool_calling_strategy: Literal["legacy", "phase1_decision"] = Field(
        default="phase1_decision"
    )
    tool_context_mode: Literal["brief_schema", "direct_schema"] = Field(
        default="direct_schema"
    )
    phase1_max_tokens: int = Field(default=128)
    phase1_direct_confidence_threshold: float = Field(default=0.55)
    max_tool_loops: int = Field(default=5)
    enable_streaming_guard: bool = Field(default=True)
    tool_debug_events: bool = Field(default=False)

    intent_provider_id: str | None = Field(default=None)
    intent_model: str | None = Field(default=None)
    tool_provider_id: str | None = Field(default=None)
    tool_model: str | None = Field(default=None)
    summary_provider_id: str | None = Field(default=None)
    summary_model: str | None = Field(default=None)
    proactive_provider_id: str | None = Field(default=None)
    proactive_model: str | None = Field(default=None)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Load TOML configuration file if it exists."""
        from pydantic_settings import TomlConfigSettingsSource

        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
