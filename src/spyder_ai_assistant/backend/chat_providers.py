"""Provider-agnostic chat backends for the chat pane.

This module keeps chat transport details out of the Qt worker and widget
layers. Each provider exposes the same two operations:

- list available chat models
- stream a chat response as chunk dictionaries

The completion provider remains Ollama-specific and does not use this module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from spyder_ai_assistant.backend.client import OllamaClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatProviderModel:
    """One provider-aware chat model entry for the unified selector."""

    provider_id: str
    provider_label: str
    name: str
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    size_gb: float = 0.0

    def to_payload(self):
        """Return one serializable dict for the UI layer."""
        return {
            "provider_id": self.provider_id,
            "provider_label": self.provider_label,
            "name": self.name,
            "family": self.family,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
            "size_gb": self.size_gb,
        }


class BaseChatProvider:
    """Small common interface for chat backends."""

    provider_id = ""
    provider_label = ""

    def is_configured(self):
        """Return True when the provider should participate in discovery."""
        raise NotImplementedError

    def list_models(self):
        """Return available chat models as ``ChatProviderModel`` entries."""
        raise NotImplementedError

    def chat_stream(self, model, messages, options=None):
        """Yield streaming chat chunks in the worker's common format."""
        raise NotImplementedError


class OllamaChatProvider(BaseChatProvider):
    """Chat backend backed by the local Ollama API."""

    provider_id = "ollama"
    provider_label = "Ollama"

    def __init__(self, host):
        self._host = host or "http://localhost:11434"
        self._client = OllamaClient(host=self._host)

    def is_configured(self):
        """Ollama is always available when a host is configured."""
        return bool(self._host)

    def list_models(self):
        """Return local Ollama models for the chat selector."""
        return [
            ChatProviderModel(
                provider_id=self.provider_id,
                provider_label=self.provider_label,
                name=model["name"],
                family=model.get("family", ""),
                parameter_size=model.get("parameter_size", ""),
                quantization=model.get("quantization", ""),
                size_gb=model.get("size_gb", 0.0),
            )
            for model in self._client.list_models()
        ]

    def chat_stream(self, model, messages, options=None):
        """Proxy Ollama streaming chunks unchanged."""
        yield from self._client.chat_stream(model, messages, options)


class OpenAICompatibleChatProvider(BaseChatProvider):
    """Generic OpenAI-compatible chat provider using direct HTTP calls."""

    provider_id = "openai_compatible"
    provider_label = "OpenAI-compatible"

    def __init__(self, base_url, api_key=""):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._client = None
        if self._base_url:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.Client(
                base_url=f"{self._base_url}/v1",
                headers=headers,
                timeout=30.0,
            )

    def is_configured(self):
        """Return True when a compatible endpoint has been configured."""
        return bool(self._base_url)

    def list_models(self):
        """Fetch models from one OpenAI-compatible `/v1/models` endpoint."""
        if not self.is_configured():
            return []

        if self._client is None:
            raise RuntimeError("OpenAI-compatible client is not initialized")

        response = self._client.get("/models")
        response.raise_for_status()
        payload = response.json()
        models = []
        for item in payload.get("data", []):
            models.append(
                ChatProviderModel(
                    provider_id=self.provider_id,
                    provider_label=self.provider_label,
                    name=item.get("id", ""),
                    family=item.get("owned_by", ""),
                )
            )
        models.sort(key=lambda model: model.name)
        return models

    def chat_stream(self, model, messages, options=None):
        """Stream an OpenAI-compatible `/v1/chat/completions` response."""
        if not self.is_configured():
            raise RuntimeError("OpenAI-compatible provider is not configured")
        if self._client is None:
            raise RuntimeError("OpenAI-compatible client is not initialized")

        options = dict(options or {})
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if "num_predict" in options:
            payload["max_tokens"] = options["num_predict"]

        usage = {}
        with self._client.stream(
            "POST",
            "/chat/completions",
            json=payload,
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue

                line = (
                    raw_line.decode("utf-8")
                    if isinstance(raw_line, bytes)
                    else str(raw_line)
                )
                if not line.startswith("data: "):
                    continue

                data = line[6:].strip()
                if data == "[DONE]":
                    break

                chunk = json.loads(data)
                usage = chunk.get("usage") or usage
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                content = delta.get("content", "") or ""
                finish_reason = choice.get("finish_reason")

                if content:
                    yield {"content": content, "done": False}

                if finish_reason:
                    yield {
                        "content": "",
                        "done": True,
                        "eval_count": int(usage.get("completion_tokens", 0) or 0),
                        "eval_duration": 0,
                        "prompt_eval_count": int(usage.get("prompt_tokens", 0) or 0),
                    }
                    return

        yield {
            "content": "",
            "done": True,
            "eval_count": int(usage.get("completion_tokens", 0) or 0),
            "eval_duration": 0,
            "prompt_eval_count": int(usage.get("prompt_tokens", 0) or 0),
        }


class ChatProviderRegistry:
    """Build and dispatch the set of configured chat providers."""

    def __init__(self, settings=None):
        self._settings = dict(settings or {})
        self._providers = self._build_providers()

    def list_models(self):
        """Return all models from configured providers in one flat list."""
        models = []
        for provider in self._providers.values():
            if not provider.is_configured():
                continue
            try:
                for model in provider.list_models():
                    if hasattr(model, "to_payload"):
                        models.append(model.to_payload())
                    else:
                        payload = dict(model or {})
                        payload.setdefault("provider_id", provider.provider_id)
                        payload.setdefault(
                            "provider_label",
                            provider.provider_label,
                        )
                        models.append(payload)
            except Exception as error:
                logger.warning(
                    "Failed to list models for provider %s: %s",
                    provider.provider_id,
                    error,
                )
        models.sort(key=lambda model: (model["provider_label"], model["name"]))
        return models

    def chat_stream(self, provider_id, model, messages, options=None):
        """Stream one chat response from the requested provider."""
        provider = self._providers.get(provider_id)
        if provider is None:
            raise RuntimeError(f"Unknown chat provider: {provider_id}")
        yield from provider.chat_stream(model, messages, options)

    def _build_providers(self):
        """Instantiate every known chat provider from one settings snapshot."""
        providers = {
            OllamaChatProvider.provider_id: OllamaChatProvider(
                self._settings.get("ollama_host", "http://localhost:11434")
            ),
            OpenAICompatibleChatProvider.provider_id: OpenAICompatibleChatProvider(
                self._settings.get("openai_compatible_base_url", ""),
                api_key=self._settings.get("openai_compatible_api_key", ""),
            ),
        }
        return providers
