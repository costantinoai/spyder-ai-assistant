"""Helpers for named chat-provider profiles."""

from __future__ import annotations

import json
import uuid


PROVIDER_KIND_OLLAMA = "ollama"
PROVIDER_KIND_OPENAI_COMPATIBLE = "openai_compatible"
DEFAULT_COMPATIBLE_PROFILE_LABEL = "Compatible endpoint"


def make_provider_profile(
    *,
    profile_id=None,
    label="",
    provider_kind=PROVIDER_KIND_OPENAI_COMPATIBLE,
    base_url="",
    api_key="",
    enabled=True,
):
    """Return one normalized provider-profile record."""
    provider_kind = (
        str(provider_kind or PROVIDER_KIND_OPENAI_COMPATIBLE).strip()
        or PROVIDER_KIND_OPENAI_COMPATIBLE
    )
    normalized_label = str(label or "").strip()
    if not normalized_label:
        if provider_kind == PROVIDER_KIND_OPENAI_COMPATIBLE:
            normalized_label = DEFAULT_COMPATIBLE_PROFILE_LABEL
        else:
            normalized_label = provider_kind.title()
    return {
        "profile_id": str(profile_id or _new_profile_id()).strip(),
        "label": normalized_label,
        "provider_kind": provider_kind,
        "base_url": str(base_url or "").strip(),
        "api_key": str(api_key or ""),
        "enabled": bool(enabled),
    }


def normalize_provider_profiles(raw_profiles, legacy_base_url="", legacy_api_key=""):
    """Return normalized provider profiles from config-backed raw data."""
    if isinstance(raw_profiles, str):
        try:
            raw_profiles = json.loads(raw_profiles or "[]")
        except Exception:
            raw_profiles = []
    if not isinstance(raw_profiles, list):
        raw_profiles = []

    profiles = []
    seen = set()
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, dict):
            continue
        profile = make_provider_profile(
            profile_id=raw_profile.get("profile_id"),
            label=raw_profile.get("label", ""),
            provider_kind=raw_profile.get(
                "provider_kind",
                PROVIDER_KIND_OPENAI_COMPATIBLE,
            ),
            base_url=raw_profile.get("base_url", ""),
            api_key=raw_profile.get("api_key", ""),
            enabled=raw_profile.get("enabled", True),
        )
        if profile["profile_id"] in seen:
            profile["profile_id"] = _new_profile_id()
        seen.add(profile["profile_id"])
        profiles.append(profile)

    legacy_base_url = str(legacy_base_url or "").strip()
    if legacy_base_url and not _legacy_profile_exists(profiles, legacy_base_url):
        profiles.append(
            make_provider_profile(
                profile_id="legacy-compatible",
                label=DEFAULT_COMPATIBLE_PROFILE_LABEL,
                provider_kind=PROVIDER_KIND_OPENAI_COMPATIBLE,
                base_url=legacy_base_url,
                api_key=legacy_api_key,
                enabled=True,
            )
        )

    return profiles


def serialize_provider_profiles(profiles):
    """Serialize provider profiles for Spyder config persistence."""
    return json.dumps(list(profiles or []), indent=2, sort_keys=True)


def build_profile_provider_id(provider_kind, profile_id=""):
    """Return the concrete provider id used for worker dispatch."""
    provider_kind = str(provider_kind or "").strip()
    profile_id = str(profile_id or "").strip()
    if provider_kind == PROVIDER_KIND_OPENAI_COMPATIBLE and profile_id:
        return f"{provider_kind}:{profile_id}"
    return provider_kind or PROVIDER_KIND_OLLAMA


def parse_profile_provider_id(provider_id):
    """Split one concrete provider id into kind and profile id."""
    normalized = str(provider_id or "").strip()
    if ":" not in normalized:
        return normalized or PROVIDER_KIND_OLLAMA, ""
    provider_kind, profile_id = normalized.split(":", 1)
    return provider_kind.strip() or PROVIDER_KIND_OLLAMA, profile_id.strip()


def resolve_preferred_profile(profiles, profile_id=""):
    """Return the preferred enabled compatible profile, if any."""
    normalized_profile_id = str(profile_id or "").strip()
    enabled_profiles = [
        profile for profile in profiles
        if profile.get("enabled") and profile.get("base_url")
    ]
    if not enabled_profiles:
        return {}
    if normalized_profile_id:
        for profile in enabled_profiles:
            if profile.get("profile_id") == normalized_profile_id:
                return profile
    return enabled_profiles[0]


def _legacy_profile_exists(profiles, base_url):
    """Return True when a profile already points at the legacy endpoint."""
    for profile in profiles:
        if str(profile.get("base_url", "")).strip() == base_url:
            return True
    return False


def _new_profile_id():
    """Return a short random profile id."""
    return uuid.uuid4().hex[:10]
