"""AI provider abstraction layer.

Lets the script engine talk to any LLM backend through a uniform interface.
Select a provider at runtime with the AI_PROVIDER env var (defaults to Groq).

Most providers (Groq, OpenAI, Ollama, Gemini, Mistral) speak the OpenAI
chat-completions protocol and share a common base class. Anthropic uses its own
native client (system prompt is a separate parameter, not a message role).
"""

import os
import asyncio
import logging
from abc import ABC, abstractmethod

from openai import AsyncOpenAI, RateLimitError

log = logging.getLogger("videoforge.providers")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AIProvider(ABC):
    """Abstract base — all providers implement this."""

    @abstractmethod
    async def complete(self, system: str, user: str,
                       max_tokens: int = 6000,
                       temperature: float = 0.7) -> str:
        """Send prompt, return raw text response."""
        ...

    @abstractmethod
    async def fast_complete(self, user: str,
                            max_tokens: int = 300) -> str:
        """Lightweight call for quick tasks (search query recovery etc)."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Shared OpenAI-compatible implementation
# ---------------------------------------------------------------------------

class _OpenAICompatProvider(AIProvider):
    """Base for any provider exposing an OpenAI-compatible chat endpoint."""

    _label = "OpenAI-compatible"
    _complete_model = ""
    _fast_model = ""
    _max_tokens_cap: int | None = None
    _backoffs: list[int] = []  # seconds between retries; [] disables retry

    def __init__(self, api_key: str | None, base_url: str | None = None):
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)

    def _retry_exceptions(self) -> tuple[type[BaseException], ...]:
        return (RateLimitError,)

    async def _chat(self, model, messages, max_tokens, temperature) -> str:
        retry_excs = self._retry_exceptions()
        for attempt in range(len(self._backoffs) + 1):
            try:
                resp = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return (resp.choices[0].message.content or "").strip()
            except retry_excs:
                if attempt < len(self._backoffs):
                    wait = self._backoffs[attempt]
                    log.warning("%s rate-limited; retrying in %ds (attempt %d/%d)",
                                self._label, wait, attempt + 1, len(self._backoffs))
                    await asyncio.sleep(wait)
                else:
                    raise

    async def complete(self, system, user, max_tokens=6000, temperature=0.7) -> str:
        if self._max_tokens_cap:
            max_tokens = min(max_tokens, self._max_tokens_cap)
        return await self._chat(
            self._complete_model,
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens, temperature,
        )

    async def fast_complete(self, user, max_tokens=300) -> str:
        return await self._chat(
            self._fast_model,
            [{"role": "user", "content": user}],
            max_tokens, 0.3,
        )

    @property
    def provider_name(self) -> str:
        return f"{self._label} ({self._complete_model})"


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------

class GroqProvider(_OpenAICompatProvider):
    """Groq — free tier, fastest. Default provider."""

    _label = "Groq"
    _complete_model = "llama-3.3-70b-versatile"
    _fast_model = "llama-3.1-8b-instant"
    _max_tokens_cap = 6000          # free-tier limit
    _backoffs = [10, 20]            # retry up to 3 attempts on rate limit

    def __init__(self):
        super().__init__(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )


class OpenAIProvider(_OpenAICompatProvider):
    """OpenAI — official endpoint, no base_url override."""

    _label = "OpenAI"
    _complete_model = "gpt-4o-mini"
    _fast_model = "gpt-4o-mini"

    def __init__(self):
        super().__init__(api_key=os.getenv("OPENAI_API_KEY"))


class OllamaProvider(_OpenAICompatProvider):
    """Ollama — fully local, zero cost. No rate limiting."""

    _label = "Ollama"

    def __init__(self):
        model = os.getenv("OLLAMA_MODEL", "llama3.2")
        self._complete_model = model
        self._fast_model = model  # no cost distinction locally
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        base_url = host if host.endswith("/v1") else f"{host}/v1"
        # Ollama ignores the key, but the openai client requires a non-empty one.
        super().__init__(api_key="ollama", base_url=base_url)


class GeminiProvider(_OpenAICompatProvider):
    """Google Gemini — OpenAI-compatible endpoint, generous free tier."""

    _label = "Gemini"
    _complete_model = "gemini-2.0-flash"
    _fast_model = "gemini-2.0-flash"
    _backoffs = [10, 20]

    def __init__(self):
        super().__init__(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        )

    def _retry_exceptions(self) -> tuple[type[BaseException], ...]:
        excs: list[type[BaseException]] = [RateLimitError]
        # Treat google.api_core quota/availability errors the same as rate limits.
        try:
            from google.api_core import exceptions as gexc  # type: ignore
            excs += [gexc.ResourceExhausted, gexc.TooManyRequests,
                     gexc.ServiceUnavailable]
        except Exception:  # noqa: BLE001 — optional dependency
            pass
        return tuple(excs)


class MistralProvider(_OpenAICompatProvider):
    """Mistral — OpenAI-compatible endpoint, free tier available."""

    _label = "Mistral"
    _complete_model = "mistral-small-latest"
    _fast_model = "mistral-small-latest"
    _backoffs = [10, 20]

    def __init__(self):
        super().__init__(
            api_key=os.getenv("MISTRAL_API_KEY"),
            base_url="https://api.mistral.ai/v1",
        )


class AnthropicProvider(AIProvider):
    """Anthropic Claude — native client (system prompt is a separate param)."""

    _label = "Anthropic"
    _model = "claude-haiku-4-5"
    _backoffs = [10, 20]

    def __init__(self):
        from anthropic import AsyncAnthropic  # lazy import
        self.client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    async def _message(self, system: str | None, user: str,
                       max_tokens: int, temperature: float) -> str:
        import anthropic
        for attempt in range(len(self._backoffs) + 1):
            try:
                kwargs: dict = {
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": user}],
                }
                if system:
                    kwargs["system"] = system
                resp = await self.client.messages.create(**kwargs)
                # Concatenate any text blocks in the response.
                return "".join(
                    block.text for block in resp.content
                    if getattr(block, "type", None) == "text"
                ).strip()
            except anthropic.RateLimitError:
                if attempt < len(self._backoffs):
                    wait = self._backoffs[attempt]
                    log.warning("Anthropic rate-limited; retrying in %ds (attempt %d/%d)",
                                wait, attempt + 1, len(self._backoffs))
                    await asyncio.sleep(wait)
                else:
                    raise

    async def complete(self, system, user, max_tokens=6000, temperature=0.7) -> str:
        return await self._message(system, user, max_tokens, temperature)

    async def fast_complete(self, user, max_tokens=300) -> str:
        return await self._message(None, user, max_tokens, 0.3)

    @property
    def provider_name(self) -> str:
        return f"Anthropic ({self._model})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> AIProvider:
    """Read AI_PROVIDER env var and return the right provider.

    Falls back to Groq if not set.
    """
    provider = os.getenv("AI_PROVIDER", "groq").lower()

    providers = {
        "groq":      GroqProvider,
        "openai":    OpenAIProvider,
        "ollama":    OllamaProvider,
        "gemini":    GeminiProvider,
        "mistral":   MistralProvider,
        "anthropic": AnthropicProvider,
    }

    if provider not in providers:
        raise ValueError(
            f"Unknown AI_PROVIDER '{provider}'. "
            f"Choose from: {', '.join(providers.keys())}"
        )

    return providers[provider]()
