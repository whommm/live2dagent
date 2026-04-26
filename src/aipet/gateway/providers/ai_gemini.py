"""Google Gemini AI Provider implementation using google-genai."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from google import genai

from aipet.gateway.providers.ai import Chunk, Message, ProviderToolCapabilities, Tool


class GeminiProvider:
    """Google Gemini API provider (google-genai)."""

    def __init__(self, api_key: str, model_id: str = "gemini-2.5-flash-preview-05-20") -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._client = genai.Client(api_key=api_key)

    @property
    def name(self) -> str:
        return "Gemini"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calling(self) -> bool:
        return True

    @property
    def tool_capabilities(self) -> ProviderToolCapabilities:
        return ProviderToolCapabilities(
            supports_native_tool_calling=True,
            supports_streaming_tool_calls=True,
            supports_tool_result_roundtrip=False,
            supports_structured_json=False,
        )

    def _to_contents(self, messages: list[Message]) -> list[Any]:
        """Convert internal Message list to google-genai contents format."""
        contents: list[Any] = []
        for msg in messages:
            if msg.role == "user":
                contents.append(msg.content)
            elif msg.role == "assistant":
                contents.append(
                    genai.types.Content(
                        role="model",
                        parts=[genai.types.Part(text=msg.content)],
                    )
                )
            elif msg.role == "system":
                # System messages are handled via config.system_instruction
                pass
            elif msg.role == "tool":
                contents.append(
                    genai.types.Content(
                        role="user",
                        parts=[genai.types.Part(text=msg.content)],
                    )
                )
        return contents

    @staticmethod
    def _to_gemini_tools(tools: list[Tool] | None) -> list[Any] | None:
        """Convert internal Tool list to Gemini types.Tool format."""
        if not tools:
            return None
        gemini_tools: list[Any] = []
        for t in tools:
            func = t.function
            params = func.get("parameters", {})
            gemini_tools.append(
                genai.types.Tool(
                    function_declarations=[
                        genai.types.FunctionDeclaration(
                            name=func["name"],
                            description=func["description"],
                            parameters=params or None,
                        )
                    ]
                )
            )
        return gemini_tools

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        """Stream a chat response."""
        # Extract system instruction if present
        system_instruction = ""
        conversation = []
        for msg in messages:
            if msg.role == "system":
                system_instruction += msg.content + "\n"
            else:
                conversation.append(msg)

        contents = self._to_contents(conversation)
        gemini_tools = self._to_gemini_tools(tools)

        config = genai.types.GenerateContentConfig(
            system_instruction=system_instruction.strip() or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=gemini_tools,
        )

        async def _stream() -> AsyncIterator[Chunk]:
            response = await self._client.aio.models.generate_content_stream(
                model=self._model_id,
                contents=contents,
                config=config,
            )
            tool_calls: list[dict[str, Any]] = []
            async for chunk in response:
                text = chunk.text if hasattr(chunk, "text") else ""
                if text:
                    yield Chunk(delta=text)

                # Accumulate function calls from all candidates/parts
                for candidate in getattr(chunk, "candidates", None) or []:
                    content = getattr(candidate, "content", None)
                    if content:
                        for part in getattr(content, "parts", None) or []:
                            fc = getattr(part, "function_call", None)
                            if fc:
                                args: dict[str, Any] = {}
                                fc_args = getattr(fc, "args", None)
                                if fc_args:
                                    args = dict(fc_args.items())
                                tool_calls.append(
                                    {
                                        "name": getattr(fc, "name", ""),
                                        "arguments": args,
                                    }
                                )

            if tool_calls:
                yield Chunk(delta="", finish_reason="tool_calls", tool_calls=tool_calls)
            else:
                yield Chunk(delta="", finish_reason="stop")

        return _stream()

    async def compact(self, messages: list[Message]) -> str:
        prompt = (
            "Summarize the following conversation into a short paragraph "
            "highlighting key facts and user preferences:\n\n"
        )
        for msg in messages:
            prompt += f"{msg.role}: {msg.content}\n"
        prompt += "\nSummary:"

        response = await self._client.aio.models.generate_content(
            model=self._model_id,
            contents=prompt,
        )
        text = response.text if hasattr(response, "text") else ""
        return text or ""
