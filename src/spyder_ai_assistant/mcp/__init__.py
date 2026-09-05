"""Spyder MCP transport helpers.

Expose settings helpers eagerly, but keep the Qt-dependent bridge, server, and
launch helpers lazy so settings-only imports do not require a Qt environment.
"""

from importlib import import_module

from spyder_ai_assistant.mcp.settings import (
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PATH,
    DEFAULT_MCP_PORT,
    DEFAULT_MCP_SERVER_NAME,
    build_client_setup_snippets,
    build_claude_mcp_config,
    build_codex_mcp_override,
    build_mcp_endpoint_url,
    build_opencode_mcp_config,
    normalize_mcp_host,
    normalize_mcp_port,
)

__all__ = [
    "DEFAULT_MCP_HOST",
    "DEFAULT_MCP_PATH",
    "DEFAULT_MCP_PORT",
    "DEFAULT_MCP_SERVER_NAME",
    "MCP_CLIENT_CLAUDE",
    "MCP_CLIENT_CODEX",
    "MCP_CLIENT_OPENCODE",
    "SUPPORTED_MCP_CLIENTS",
    "SpyderMCPBridge",
    "SpyderMCPServer",
    "build_client_setup_snippets",
    "build_claude_mcp_config",
    "build_codex_mcp_override",
    "build_mcp_endpoint_url",
    "build_opencode_mcp_config",
    "get_mcp_client_label",
    "launch_mcp_client_in_terminal",
    "normalize_mcp_host",
    "normalize_mcp_port",
]


def __getattr__(name):
    """Resolve Qt-dependent MCP helpers lazily."""
    if name in {"SpyderMCPBridge", "SpyderMCPServer"}:
        bridge = import_module("spyder_ai_assistant.mcp.bridge")
        server = import_module("spyder_ai_assistant.mcp.server")

        mapping = {
            "SpyderMCPBridge": bridge.SpyderMCPBridge,
            "SpyderMCPServer": server.SpyderMCPServer,
        }
        return mapping[name]

    if name in {
        "MCP_CLIENT_CLAUDE",
        "MCP_CLIENT_CODEX",
        "MCP_CLIENT_OPENCODE",
        "SUPPORTED_MCP_CLIENTS",
        "get_mcp_client_label",
        "launch_mcp_client_in_terminal",
    }:
        launch = import_module("spyder_ai_assistant.mcp.launch")

        mapping = {
            "MCP_CLIENT_CLAUDE": launch.MCP_CLIENT_CLAUDE,
            "MCP_CLIENT_CODEX": launch.MCP_CLIENT_CODEX,
            "MCP_CLIENT_OPENCODE": launch.MCP_CLIENT_OPENCODE,
            "SUPPORTED_MCP_CLIENTS": launch.SUPPORTED_MCP_CLIENTS,
            "get_mcp_client_label": launch.get_mcp_client_label,
            "launch_mcp_client_in_terminal": launch.launch_mcp_client_in_terminal,
        }
        return mapping[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
