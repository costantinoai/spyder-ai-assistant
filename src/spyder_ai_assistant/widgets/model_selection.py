"""Shared helpers for provider-aware model dropdowns.

The chat toolbar and the Assistant Settings dialog both list discovered
models (payload dicts from the provider registry), label them, and pick a
"best" entry from configured preferences. Keeping that logic here means one
labelling scheme and one selection priority everywhere.

Payload keys used: ``name``, ``provider_id``, ``provider_kind``,
``provider_label``, ``profile_id``, ``parameter_size``, ``size_gb``,
``family``, ``quantization``, ``endpoint``.
"""

from __future__ import annotations

from qtpy.QtCore import Qt

from spyder_ai_assistant.utils.provider_profiles import (
    PROVIDER_KIND_OPENAI_COMPATIBLE,
)


def format_model_display(payload, show_provider=True):
    """Return the dropdown label for one model.

    The provider prefix only earns its space when models from more than one
    provider are listed; with a single provider it just truncates the model
    name in a narrow dock.
    """
    provider_label = payload.get("provider_label", "Provider")
    name = payload.get("name", "")
    parameter_size = payload.get("parameter_size", "")
    size_gb = payload.get("size_gb", 0) or 0

    details = []
    if parameter_size:
        details.append(str(parameter_size))
    if size_gb:
        details.append(f"{size_gb}GB")
    label = f"{name} ({', '.join(details)})" if details else name
    if show_provider:
        return f"[{provider_label}] {label}"
    return label


def format_model_tooltip(payload):
    """Return the detailed tooltip for one provider-aware model entry."""
    lines = [
        f"Provider: {payload.get('provider_label', 'unknown')}",
        f"Kind: {payload.get('provider_kind', payload.get('provider_id', 'unknown'))}",
        f"Model: {payload.get('name', '')}",
        f"Family: {payload.get('family', 'unknown') or 'unknown'}",
        f"Parameters: {payload.get('parameter_size', 'unknown') or 'unknown'}",
        f"Quantization: {payload.get('quantization', 'unknown') or 'unknown'}",
        f"Size: {payload.get('size_gb', 0) or 0} GB",
    ]
    if payload.get("endpoint"):
        lines.append(f"Endpoint: {payload.get('endpoint', '')}")
    return "\n".join(lines)


def provider_key(payload):
    """Return the key that groups models served by the same provider setup."""
    return (
        payload.get("provider_kind", payload.get("provider_id", "")),
        payload.get("profile_id", ""),
        payload.get("provider_id", ""),
    )


def uses_multiple_providers(models):
    """True when the discovered models come from more than one provider."""
    return len({model.get("provider_id", "") for model in models}) > 1


def populate_model_combo(combo, models, show_provider=None, placeholder=""):
    """Fill ``combo`` with model payloads (label, payload data, tooltip).

    ``show_provider`` defaults to "only when several providers are listed".
    With no models, ``placeholder`` (if given) is added as a single disabled
    row so the control still explains itself.
    """
    models = [dict(model) for model in (models or []) if isinstance(model, dict)]
    if show_provider is None:
        show_provider = uses_multiple_providers(models)
    combo.blockSignals(True)
    try:
        combo.clear()
        for payload in models:
            combo.addItem(format_model_display(payload, show_provider), payload)
            combo.setItemData(
                combo.count() - 1, format_model_tooltip(payload), Qt.ToolTipRole
            )
        if not models and placeholder:
            combo.addItem(placeholder, None)
    finally:
        combo.blockSignals(False)
    return models


def _combo_payloads(combo):
    for index in range(combo.count()):
        payload = combo.itemData(index)
        if isinstance(payload, dict):
            yield index, payload


def find_model_index(combo, name="", provider_kind="", profile_id="", previous=None):
    """Return the index of the best matching model row, or -1 when empty.

    Priority: the previously selected payload (identity across a refresh),
    then an exact name + provider kind (+ profile for compatible endpoints),
    then any model of the preferred compatible profile, then the name on any
    provider, then the first row.
    """
    if isinstance(previous, dict):
        for index, payload in _combo_payloads(combo):
            if payload == previous:
                return index

    for index, payload in _combo_payloads(combo):
        if name and payload.get("name") != name:
            continue
        kind = payload.get("provider_kind", payload.get("provider_id", ""))
        if provider_kind and kind != provider_kind:
            continue
        if (
            kind == PROVIDER_KIND_OPENAI_COMPATIBLE
            and profile_id
            and payload.get("profile_id") != profile_id
        ):
            continue
        if name:
            return index

    if provider_kind == PROVIDER_KIND_OPENAI_COMPATIBLE and profile_id:
        for index, payload in _combo_payloads(combo):
            if (
                payload.get("provider_kind") == PROVIDER_KIND_OPENAI_COMPATIBLE
                and payload.get("profile_id") == profile_id
            ):
                return index

    if name:
        for index, payload in _combo_payloads(combo):
            if payload.get("name") == name:
                return index

    return 0 if combo.count() > 0 else -1


def select_model(combo, name="", provider_kind="", profile_id="", previous=None):
    """Select the best matching row (see ``find_model_index``); return it."""
    index = find_model_index(
        combo, name=name, provider_kind=provider_kind,
        profile_id=profile_id, previous=previous,
    )
    if index >= 0:
        combo.setCurrentIndex(index)
    return index
