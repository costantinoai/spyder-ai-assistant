"""Shared MCP settings and client setup helpers."""

from __future__ import annotations

import json

DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8769
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_MCP_SERVER_NAME = "spyder"
OPENCODE_CONFIG_SCHEMA_URL = "https://opencode.ai/config.json"


def normalize_mcp_host(host):
    """Return one normalized listen host for the embedded MCP server."""
    normalized = str(host or "").strip()
    return normalized or DEFAULT_MCP_HOST


def normalize_mcp_port(port):
    """Return one safe listen port for the embedded MCP server."""
    try:
        normalized = int(port)
    except (TypeError, ValueError):
        return DEFAULT_MCP_PORT

    if 1 <= normalized <= 65535:
        return normalized
    return DEFAULT_MCP_PORT


def build_mcp_endpoint_url(host=None, port=None, path=DEFAULT_MCP_PATH):
    """Return the HTTP endpoint URL for the embedded MCP server."""
    normalized_path = "/" + str(path or DEFAULT_MCP_PATH).lstrip("/")
    return (
        f"http://{normalize_mcp_host(host)}:"
        f"{normalize_mcp_port(port)}{normalized_path}"
    )


def build_claude_mcp_add_command(url, name=DEFAULT_MCP_SERVER_NAME):
    """Return the Claude Code command for adding the Spyder MCP server."""
    return f"claude mcp add --transport http {name} {url}"


def build_claude_mcp_config(url, name=DEFAULT_MCP_SERVER_NAME):
    """Return a Claude-compatible MCP config payload."""
    return {
        "mcpServers": {
            str(name or DEFAULT_MCP_SERVER_NAME): {
                "type": "http",
                "url": str(url or ""),
            },
        },
    }


def build_codex_mcp_add_command(url, name=DEFAULT_MCP_SERVER_NAME):
    """Return the Codex CLI command for adding the Spyder MCP server."""
    return f"codex mcp add {name} --url {url}"


def build_codex_mcp_override(url, name=DEFAULT_MCP_SERVER_NAME):
    """Return one inline Codex config override for the Spyder MCP server."""
    encoded_name = str(name or DEFAULT_MCP_SERVER_NAME).replace('"', "")
    encoded_url = str(url or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'mcp_servers.{encoded_name}.url="{encoded_url}"'


def build_opencode_mcp_payload(url, name=DEFAULT_MCP_SERVER_NAME):
    """Return an OpenCode MCP payload for the Spyder MCP server."""
    return {
        "$schema": OPENCODE_CONFIG_SCHEMA_URL,
        "mcp": {
            str(name or DEFAULT_MCP_SERVER_NAME): {
                "type": "remote",
                "url": str(url or ""),
                "enabled": True,
            },
        },
    }


def build_opencode_mcp_config(url, name=DEFAULT_MCP_SERVER_NAME):
    """Return an OpenCode config snippet for the Spyder MCP server."""
    payload = build_opencode_mcp_payload(url, name=name)
    return json.dumps(payload, indent=2)


def build_client_setup_snippets(host=None, port=None, name=DEFAULT_MCP_SERVER_NAME):
    """Return the endpoint URL plus copy-ready client setup snippets."""
    url = build_mcp_endpoint_url(host=host, port=port)
    return {
        "name": str(name or DEFAULT_MCP_SERVER_NAME),
        "url": url,
        "claude_command": build_claude_mcp_add_command(url, name=name),
        "codex_command": build_codex_mcp_add_command(url, name=name),
        "opencode_config": build_opencode_mcp_config(url, name=name),
    }
