"""Factory for instantiating providers based on configuration."""

from __future__ import annotations

from aipet.gateway.config import GatewayConfig
from aipet.gateway.providers.ai import AIProvider
from aipet.gateway.providers.ai_echo import EchoProvider


def create_ai_provider(config: GatewayConfig) -> AIProvider:
    """Create an AI provider from configuration."""
    if config.ai_provider == "gemini":
        from aipet.gateway.providers.ai_gemini import GeminiProvider

        if not config.ai_api_key:
            print("[WARNING] Gemini provider requires ai_api_key. Falling back to EchoProvider.")
            return EchoProvider(model_id="echo")
        return GeminiProvider(api_key=config.ai_api_key, model_id=config.ai_model)
    elif config.ai_provider == "openai":
        from aipet.gateway.providers.ai_openai import OpenAIProvider

        if not config.ai_api_key:
            print("[WARNING] OpenAI provider requires ai_api_key. Falling back to EchoProvider.")
            return EchoProvider(model_id="echo")
        return OpenAIProvider(api_key=config.ai_api_key, model_id=config.ai_model)
    elif config.ai_provider == "anthropic":
        from aipet.gateway.providers.ai_anthropic import AnthropicProvider

        if not config.ai_api_key:
            print("[WARNING] Anthropic provider requires ai_api_key. Falling back to EchoProvider.")
            return EchoProvider(model_id="echo")
        return AnthropicProvider(api_key=config.ai_api_key, model_id=config.ai_model)
    elif config.ai_provider == "ollama":
        from aipet.gateway.providers.ai_ollama import OllamaProvider

        return OllamaProvider(model_id=config.ai_model)
    else:
        # Fallback to echo for testing
        return EchoProvider(model_id="echo")
