"""Cached editor/project context service shared by chat and MCP.

Wraps the stateless context utilities in ``utils.context`` with bounded
caching so that repeated requests (e.g. multiple chat sends or MCP tool
calls within a short window) do not redundantly walk the filesystem or
re-read unchanged editor tabs.

Caching strategy:
- **Active file**: Never cached.  Cursor, selection, and content must
  reflect the exact editor state at request time.
- **Open files summaries**: Cached with a short TTL, keyed by the
  active filename.  Invalidated by ``sig_codeeditor_changed`` and
  ``sig_codeeditor_created`` (wired in ``plugin.py``).
- **Project tree**: Cached with a longer TTL, keyed by project path.
  Invalidated by ``sig_project_loaded`` and ``sig_project_closed``.

Both chat (system prompt assembly) and MCP (bridge tool handlers) use
the same service instance, created once in ``plugin.py:on_initialize``.
All access happens on the Qt main thread, so no locking is needed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from spyder_ai_assistant.utils.context import (
    get_editor_context,
    get_open_files_context,
    get_project_context,
)

logger = logging.getLogger(__name__)

# --- TTL constants (seconds) ---

# Open-files summaries rarely change between rapid sends.  5 s is short
# enough to stay reasonably fresh yet long enough to absorb bursts.
OPEN_FILES_TTL: float = 5.0

# Project tree changes even less often during active editing.  60 s
# avoids repeated filesystem walks while still picking up new files
# within a minute.
PROJECT_TTL: float = 60.0


# ---------------------------------------------------------------------------
# Cache slot
# ---------------------------------------------------------------------------

@dataclass
class _CacheSlot:
    """One bounded-TTL cache entry.

    Attributes:
        value: The cached result, or *None* if the slot is empty.
        timestamp: ``time.monotonic()`` of the last ``store()`` call.
        key: Optional qualifier so a key mismatch (e.g. the active
            filename changed) also counts as a miss.
    """

    value: Any = None
    timestamp: float = 0.0
    key: Optional[str] = None

    def is_valid(self, ttl: float, key: Optional[str] = None) -> bool:
        """Return *True* if the slot holds a usable value.

        A slot is valid when all of:
        1. ``value`` is not *None* (slot has been populated).
        2. Age is within *ttl* seconds.
        3. If *key* is given, it matches the stored key.
        """
        if self.value is None:
            return False
        if key is not None and self.key != key:
            return False
        return (time.monotonic() - self.timestamp) < ttl

    def store(self, value: Any, key: Optional[str] = None) -> None:
        """Populate the slot with a fresh value."""
        self.value = value
        self.timestamp = time.monotonic()
        self.key = key

    def clear(self) -> None:
        """Invalidate the slot so the next access triggers a rebuild."""
        self.value = None
        self.timestamp = 0.0
        self.key = None


# ---------------------------------------------------------------------------
# Context service
# ---------------------------------------------------------------------------

class EditorContextService:
    """Cached context service shared by chat and MCP.

    Wraps the stateless utilities in ``utils.context`` with bounded
    caching.  The service is a plain class (not a QObject) because it
    is only accessed from the Qt main thread.

    Args:
        editor_resolver: Callable that returns the Spyder Editor plugin
            instance (or *None* if unavailable).
        projects_resolver: Callable that returns the Spyder Projects
            plugin instance (or *None* if unavailable).
    """

    def __init__(
        self,
        editor_resolver: Callable[[], Any],
        projects_resolver: Callable[[], Any],
    ) -> None:
        self._editor_resolver = editor_resolver
        self._projects_resolver = projects_resolver

        # Cache slots
        self._open_files = _CacheSlot()
        self._project_tree = _CacheSlot()

    # --- public API ---------------------------------------------------------

    def get_full_context(self) -> dict[str, Any]:
        """Return the complete context dict for chat system prompt assembly.

        Shape matches the dict previously returned by
        ``plugin._get_editor_context()``:

        .. code-block:: python

            {
                "context":    dict,   # active file (always fresh)
                "open_files": list,   # other open tabs (cached)
                "project":    dict,   # project tree (cached)
                "console":    dict,   # reserved, always empty here
            }
        """
        current_file = self.get_current_file()
        current_filename = current_file.get("filename", "")

        return {
            "context": current_file,
            "open_files": self.get_open_files(current_filename=current_filename),
            "project": self.get_project_tree(),
            "console": {},
        }

    def get_current_file(self) -> dict[str, Any]:
        """Return the active editor context (always fresh, never cached).

        Reads cursor position, selection, and full content from the
        current Spyder code editor.
        """
        editor_plugin = self._resolve_editor()
        if editor_plugin is None:
            return {}
        editor = editor_plugin.get_current_editor()
        return get_editor_context(editor, editor_plugin)

    def get_open_files(
        self,
        current_filename: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return summaries of non-active open editor tabs.

        Cached with a short TTL, keyed by *current_filename* so a tab
        switch automatically triggers a rebuild (the set of "other"
        files changes when the active file changes).

        Args:
            current_filename: The active file path.  If *None*, resolved
                from the editor plugin.
        """
        editor_plugin = self._resolve_editor()
        if editor_plugin is None:
            return []

        # Resolve the active filename if the caller did not supply it.
        if current_filename is None:
            try:
                current_filename = editor_plugin.get_current_filename() or ""
            except Exception:
                current_filename = ""

        # Check cache -- keyed by active filename so a tab switch
        # automatically invalidates.
        if self._open_files.is_valid(OPEN_FILES_TTL, key=current_filename):
            logger.debug("Context cache hit: open_files (key=%s)", current_filename)
            return self._open_files.value

        # Rebuild and cache.
        logger.debug("Context cache miss: open_files (key=%s)", current_filename)
        result = get_open_files_context(editor_plugin, current_filename)
        self._open_files.store(result, key=current_filename)
        return result

    def get_project_tree(self) -> dict[str, Any]:
        """Return the active project's root path and file tree.

        Cached with a longer TTL, keyed by project path so a project
        switch automatically triggers a rebuild.
        """
        projects_plugin = self._resolve_projects()

        # Determine the current project path for cache keying.
        project_path: Optional[str] = None
        if projects_plugin is not None:
            try:
                project_path = projects_plugin.get_active_project_path()
            except Exception:
                pass

        # Check cache -- keyed by project path so a project switch
        # automatically invalidates.
        if self._project_tree.is_valid(PROJECT_TTL, key=project_path or ""):
            logger.debug("Context cache hit: project_tree (key=%s)", project_path)
            return self._project_tree.value

        # Rebuild and cache.
        logger.debug("Context cache miss: project_tree (key=%s)", project_path)
        result = get_project_context(projects_plugin)
        self._project_tree.store(result, key=project_path or "")
        return result

    # --- invalidation (called from plugin signal handlers) ------------------

    def invalidate_open_files(self) -> None:
        """Clear the open-files cache.

        Called when the editor tab set changes (editor created, tab
        switched, etc.).
        """
        logger.debug("Context cache invalidated: open_files")
        self._open_files.clear()

    def invalidate_project(self) -> None:
        """Clear the project-tree cache.

        Called when a project is loaded or closed.
        """
        logger.debug("Context cache invalidated: project_tree")
        self._project_tree.clear()

    def invalidate_all(self) -> None:
        """Clear all caches."""
        self.invalidate_open_files()
        self.invalidate_project()

    # --- private helpers ----------------------------------------------------

    def _resolve_editor(self):
        """Return the Spyder Editor plugin, or *None*."""
        try:
            return self._editor_resolver()
        except Exception:
            return None

    def _resolve_projects(self):
        """Return the Spyder Projects plugin, or *None*."""
        try:
            return self._projects_resolver()
        except Exception:
            return None
