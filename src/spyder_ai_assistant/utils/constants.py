"""Constants shared across the plugin's layers.

This module deliberately imports nothing: the backend and completion paths
need the default endpoint without pulling in the settings module, which in
turn imports the MCP stack.
"""

# Default local Ollama endpoint. Owned here and re-exported by
# ``utils.assistant_settings`` for callers that already import settings.
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
