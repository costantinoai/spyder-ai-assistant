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
from spyder_ai_assistant.utils.provider_profiles import (
    DEFAULT_COMPATIBLE_PROFILE_LABEL,
    PROVIDER_KIND_OLLAMA,
    PROVIDER_KIND_OPENAI_COMPATIBLE,
    build_profile_provider_id,
    normalize_provider_profiles,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatProviderModel:
    """One provider-aware chat model entry for the unified selector."""

    provider_id: str
    provider_label: str
    name: str
    provider_kind: str = ""
    profile_id: str = ""
    endpoint: str = ""
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
            "provider_kind": self.provider_kind or self.provider_id,
            "profile_id": self.profile_id,
            "endpoint": self.endpoint,
            "family": self.family,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
            "size_gb": self.size_gb,
        }


class BaseChatProvider:
    """Small common interface for chat backends."""

    provider_id = ""
    provider_label = ""
    provider_kind = ""
    profile_id = ""
    endpoint = ""
    enabled = True

    def is_configured(self):
        """Return True when the provider should participate in discovery."""
        raise NotImplementedError

    def list_models(self):
        """Return available chat models as ``ChatProviderModel`` entries."""
        raise NotImplementedError

    def chat_stream(self, model, messages, options=None):
        """Yield streaming chat chunks in the worker's common format."""
        raise NotImplementedError

    def describe(self):
        """Return one UI-facing provider diagnostic record."""
        return {
            "provider_id": self.provider_id,
            "provider_label": self.provider_label,
            "provider_kind": self.provider_kind or self.provider_id,
            "profile_id": self.profile_id,
            "endpoint": self.endpoint,
            "enabled": bool(self.enabled),
            "configured": bool(self.is_configured()),
        }


class OllamaChatProvider(BaseChatProvider):
    """Chat backend backed by the local Ollama API."""

    provider_id = PROVIDER_KIND_OLLAMA
    provider_label = "Ollama"
    provider_kind = PROVIDER_KIND_OLLAMA

    def __init__(self, host):
        self._host = host or "http://localhost:11434"
        self._client = OllamaClient(host=self._host)
        self.endpoint = self._host

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
                provider_kind=self.provider_kind,
                endpoint=self.endpoint,
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

    provider_id = PROVIDER_KIND_OPENAI_COMPATIBLE
    provider_label = "OpenAI-compatible"
    provider_kind = PROVIDER_KIND_OPENAI_COMPATIBLE

    def __init__(
        self,
        base_url,
        api_key="",
        *,
        provider_id=None,
        provider_label=None,
        profile_id="",
        enabled=True,
    ):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self.provider_id = provider_id or self.provider_id
        self.provider_label = provider_label or self.provider_label
        self.profile_id = str(profile_id or "").strip()
        self.endpoint = self._base_url
        self.enabled = bool(enabled)
        self._client = None
        if self._base_url and self.enabled:
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
        return bool(self.enabled and self._base_url)

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
                    provider_kind=self.provider_kind,
                    profile_id=self.profile_id,
                    endpoint=self.endpoint,
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
        models, _diagnostics = self.list_models_with_diagnostics()
        return models

    def list_models_with_diagnostics(self):
        """Return models plus one diagnostic record per configured provider."""
        models = []
        diagnostics = []
        for provider in self._providers.values():
            diagnostic = provider.describe()
            try:
                if not provider.is_configured():
                    diagnostic["status"] = (
                        "disabled" if not diagnostic.get("enabled") else "unconfigured"
                    )
                    diagnostic["model_count"] = 0
                    diagnostic["message"] = (
                        "Provider disabled"
                        if not diagnostic.get("enabled")
                        else "Provider is not configured"
                    )
                    diagnostics.append(diagnostic)
                    continue
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
                        payload.setdefault(
                            "provider_kind",
                            provider.provider_kind or provider.provider_id,
                        )
                        payload.setdefault("profile_id", provider.profile_id)
                        payload.setdefault("endpoint", provider.endpoint)
                        models.append(payload)
                diagnostic["status"] = "ready"
                diagnostic["model_count"] = sum(
                    1
                    for model in models
                    if model.get("provider_id") == provider.provider_id
                )
                diagnostic["message"] = (
                    f"{diagnostic['model_count']} model(s) available"
                )
            except Exception as error:
                logger.warning(
                    "Failed to list models for provider %s: %s",
                    provider.provider_id,
                    error,
                )
                diagnostic["status"] = "error"
                diagnostic["model_count"] = 0
                diagnostic["message"] = str(error)
            diagnostics.append(diagnostic)
        models.sort(key=lambda model: (model["provider_label"], model["name"]))
        diagnostics.sort(
            key=lambda record: (
                record.get("provider_kind", ""),
                record.get("provider_label", ""),
            )
        )
        return models, diagnostics

    def chat_stream(self, provider_id, model, messages, options=None):
        """Stream one chat response from the requested provider."""
        provider = self._providers.get(provider_id)
        if provider is None:
            raise RuntimeError(f"Unknown chat provider: {provider_id}")
        yield from provider.chat_stream(model, messages, options)

    def describe_provider(self, provider_id):
        """Return one provider diagnostic-like record without probing it."""
        provider = self._providers.get(provider_id)
        if provider is None:
            return {}
        return provider.describe()

    def _build_providers(self):
        """Instantiate every known chat provider from one settings snapshot."""
        profiles = normalize_provider_profiles(
            self._settings.get("provider_profiles", []),
            legacy_base_url=self._settings.get("openai_compatible_base_url", ""),
            legacy_api_key=self._settings.get("openai_compatible_api_key", ""),
        )
        providers = {
            OllamaChatProvider.provider_id: OllamaChatProvider(
                self._settings.get("ollama_host", "http://localhost:11434")
            )
        }
        for profile in profiles:
            if profile.get("provider_kind") != PROVIDER_KIND_OPENAI_COMPATIBLE:
                continue
            provider_id = build_profile_provider_id(
                PROVIDER_KIND_OPENAI_COMPATIBLE,
                profile.get("profile_id", ""),
            )
            providers[provider_id] = OpenAICompatibleChatProvider(
                profile.get("base_url", ""),
                api_key=profile.get("api_key", ""),
                provider_id=provider_id,
                provider_label=profile.get("label", "") or DEFAULT_COMPATIBLE_PROFILE_LABEL,
                profile_id=profile.get("profile_id", ""),
                enabled=profile.get("enabled", True),
            )
        return providers
