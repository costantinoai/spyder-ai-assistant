"""Optional Spyder plugin lookup shared by the plugin and the MCP bridge.

Spyder's ``get_plugin`` raises when a plugin is unavailable, and only newer
releases accept ``error=False``. Handling both spellings was written out three
times: twice in ``plugin.py`` (once per plugin) and once in ``mcp/bridge.py``.
"""

from __future__ import annotations


def safe_get_plugin(owner, plugin_name):
    """Return one optional Spyder plugin, or None when it is unavailable.

    Args:
        owner: Any object exposing Spyder's ``get_plugin`` API.
        plugin_name: The ``Plugins`` constant to look up.

    Returns:
        The plugin instance, or None when it is missing or the lookup failed.
    """
    try:
        return owner.get_plugin(plugin_name, error=False)
    except TypeError:
        # Older Spyder releases have no ``error`` keyword.
        try:
            return owner.get_plugin(plugin_name)
        except Exception:
            return None
    except Exception:
        return None
