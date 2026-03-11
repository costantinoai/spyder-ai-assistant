"""spyder-ai-assistant: AI chat and code completion plugin for Spyder IDE."""

# Read version from package metadata (set by setuptools-scm at build time).
# This ensures the module __version__ matches the installed package version,
# which Spyder's dependency checker compares to validate external plugins.
try:
    from importlib.metadata import version as _get_version
    __version__ = _get_version("spyder-ai-assistant")
except Exception:
    __version__ = "0.0.0"
