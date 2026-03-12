"""Unit tests for named provider-profile helpers."""

from __future__ import annotations

from qtpy.QtWidgets import QApplication

from spyder_ai_assistant.utils.provider_profiles import (
    PROVIDER_KIND_OPENAI_COMPATIBLE,
    build_profile_provider_id,
    normalize_provider_profiles,
    parse_profile_provider_id,
    resolve_preferred_profile,
    serialize_provider_profiles,
)
from spyder_ai_assistant.widgets.provider_profiles_dialog import (
    ProviderProfilesDialog,
)


_QT_APP = None


def _app():
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def test_normalize_provider_profiles_migrates_legacy_endpoint():
    profiles = normalize_provider_profiles(
        "[]",
        legacy_base_url="http://127.0.0.1:8000",
        legacy_api_key="secret",
    )

    assert len(profiles) == 1
    assert profiles[0]["provider_kind"] == PROVIDER_KIND_OPENAI_COMPATIBLE
    assert profiles[0]["base_url"] == "http://127.0.0.1:8000"
    assert profiles[0]["api_key"] == "secret"


def test_serialize_round_trips_profiles():
    serialized = serialize_provider_profiles(
        [
            {
                "profile_id": "alpha",
                "label": "Research API",
                "provider_kind": PROVIDER_KIND_OPENAI_COMPATIBLE,
                "base_url": "http://127.0.0.1:8000",
                "api_key": "",
                "enabled": True,
            }
        ]
    )
    profiles = normalize_provider_profiles(serialized)

    assert profiles[0]["profile_id"] == "alpha"
    assert profiles[0]["label"] == "Research API"


def test_build_and_parse_profile_provider_id():
    provider_id = build_profile_provider_id(
        PROVIDER_KIND_OPENAI_COMPATIBLE,
        "alpha",
    )

    assert provider_id == "openai_compatible:alpha"
    assert parse_profile_provider_id(provider_id) == (
        "openai_compatible",
        "alpha",
    )


def test_resolve_preferred_profile_prefers_requested_enabled_profile():
    profiles = normalize_provider_profiles(
        [
            {
                "profile_id": "alpha",
                "label": "Alpha",
                "provider_kind": PROVIDER_KIND_OPENAI_COMPATIBLE,
                "base_url": "http://alpha",
                "enabled": True,
            },
            {
                "profile_id": "beta",
                "label": "Beta",
                "provider_kind": PROVIDER_KIND_OPENAI_COMPATIBLE,
                "base_url": "http://beta",
                "enabled": True,
            },
        ]
    )

    preferred = resolve_preferred_profile(profiles, "beta")

    assert preferred["profile_id"] == "beta"


def test_provider_profiles_dialog_allows_removing_last_profile():
    _app()
    dialog = ProviderProfilesDialog(
        profiles=[
            {
                "profile_id": "alpha",
                "label": "Alpha",
                "provider_kind": PROVIDER_KIND_OPENAI_COMPATIBLE,
                "base_url": "http://alpha",
                "enabled": True,
            }
        ]
    )

    dialog.table.selectRow(0)
    dialog._delete_profile()

    assert dialog.selected_profiles() == []
