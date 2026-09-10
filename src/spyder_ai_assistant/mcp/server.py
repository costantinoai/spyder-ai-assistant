"""Embedded HTTP MCP server for Spyder."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any


try:
    import uvicorn
except Exception as error:  # pragma: no cover - import guard
    uvicorn = None
    _UVICORN_IMPORT_ERROR = error
else:  # pragma: no cover - import guard
    _UVICORN_IMPORT_ERROR = None

try:
    from mcp.server.fastmcp import FastMCP
except Exception as error:  # pragma: no cover - import guard
    FastMCP = None
    _MCP_IMPORT_ERROR = error
else:  # pragma: no cover - import guard
    _MCP_IMPORT_ERROR = None

try:
    from starlette.applications import Starlette
    from starlette.routing import Mount
except Exception as error:  # pragma: no cover - import guard
    Starlette = None
    Mount = None
    _STARLETTE_IMPORT_ERROR = error
else:  # pragma: no cover - import guard
    _STARLETTE_IMPORT_ERROR = None

from spyder_ai_assistant.mcp.settings import (
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PATH,
    DEFAULT_MCP_PORT,
    build_mcp_endpoint_url,
    normalize_mcp_host,
    normalize_mcp_port,
)

logger = logging.getLogger(__name__)

SERVER_START_TIMEOUT_SECS = 5.0
SERVER_STOP_TIMEOUT_SECS = 5.0


@contextlib.contextmanager
def _preserve_root_logging():
    """Keep FastMCP from replacing the host application's logging setup.

    FastMCP 1.x calls ``logging.basicConfig`` from its constructor.  When
    Spyder has no root handlers yet, that installs a Rich stderr handler and
    lowers the root level to INFO.  Spyder interprets that stderr output as an
    internal error, so restore the exact root state after construction.
    """
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    try:
        yield
    finally:
        added_handlers = [
            handler
            for handler in root_logger.handlers
            if handler not in original_handlers
        ]
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        for handler in original_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(original_level)
        for handler in added_handlers:
            handler.close()


class SpyderMCPServer:
    """Run an embedded MCP server beside the Spyder UI."""

    def __init__(self, bridge, host=DEFAULT_MCP_HOST, port=DEFAULT_MCP_PORT):
        self._bridge = bridge
        self._host = normalize_mcp_host(host)
        self._port = normalize_mcp_port(port)
        self._path = DEFAULT_MCP_PATH
        self._thread = None
        self._uvicorn_server = None
        self._startup_complete = threading.Event()
        self._stop_requested = threading.Event()
        self._startup_error = None
        self._last_error = ""
        self._fastmcp = None
        self._app = None

    @property
    def endpoint_url(self):
        """Return the client connection URL."""
        return build_mcp_endpoint_url(
            host=self._host,
            port=self._port,
            path=self._path,
        )

    @property
    def host(self):
        """Return the configured listen host."""
        return self._host

    @property
    def port(self):
        """Return the configured listen port."""
        return self._port

    @property
    def last_error(self):
        """Return the last startup or dependency error, if any."""
        return self._last_error

    def start(self):
        """Start the background HTTP MCP server."""
        if self.is_running():
            return True
        if self._thread is not None and self._thread.is_alive():
            self._last_error = "The previous MCP server thread is still stopping."
            return False

        dependency_error = self._get_dependency_error()
        if dependency_error:
            self._last_error = str(dependency_error)
            logger.warning(
                "Spyder MCP server is disabled because dependencies are unavailable: %s",
                dependency_error,
            )
            return False

        self._startup_complete.clear()
        self._stop_requested.clear()
        self._startup_error = None
        self._last_error = ""
        try:
            self._fastmcp = self._build_fastmcp()
            self._app = self._build_asgi_app(self._fastmcp)
        except Exception as error:
            self._last_error = str(error)
            logger.exception("Failed to construct the Spyder MCP application")
            return False
        self._thread = threading.Thread(
            target=self._run_server,
            name="SpyderMCPServer",
            daemon=True,
        )
        self._thread.start()
        self._startup_complete.wait(timeout=SERVER_START_TIMEOUT_SECS)

        if self._startup_error is not None:
            self._last_error = str(self._startup_error)
            logger.warning(
                "Spyder MCP server failed to start on %s: %s",
                self.endpoint_url,
                self._startup_error,
            )
            return False

        if not self.is_running():
            self._last_error = (
                f"The MCP server did not finish startup on {self.endpoint_url}."
            )
            logger.warning(
                "Spyder MCP server did not finish startup on %s",
                self.endpoint_url,
            )
            self.stop(block=False)
            return False

        self._last_error = ""
        logger.info("Spyder MCP server listening on %s", self.endpoint_url)
        return True

    def stop(self, block=True):
        """Stop the background HTTP MCP server."""
        server = self._uvicorn_server
        thread = self._thread
        self._stop_requested.set()
        if server is None and thread is None:
            return

        if server is not None:
            server.should_exit = True

        # Avoid deadlocking Spyder shutdown if a request is already blocked on
        # a queued call back into the Qt main thread.
        if not block:
            logger.info("Spyder MCP server shutdown requested")
            return

        if thread is not None and thread.is_alive():
            thread.join(timeout=SERVER_STOP_TIMEOUT_SECS)

        if (
            thread is not None
            and thread.is_alive()
            and server is not None
        ):
            server.force_exit = True
            thread.join(timeout=1.0)

        if thread is not None and thread.is_alive():
            logger.warning("Spyder MCP server thread did not stop cleanly")
            # Retain ownership until termination; dropping these references
            # would allow a second server to start beside the first one.
            return
        else:
            logger.info("Spyder MCP server stopped")

        self._thread = None
        self._uvicorn_server = None
        self._fastmcp = None
        self._app = None
        self._startup_error = None
        self._startup_complete.clear()

    def is_running(self):
        """Return True when the HTTP server is accepting requests."""
        thread = self._thread
        server = self._uvicorn_server
        return bool(
            thread is not None
            and thread.is_alive()
            and server is not None
            and getattr(server, "started", False)
            and not self._stop_requested.is_set()
        )

    def is_stopping(self):
        """Return whether a stopped service still owns a live thread."""
        return bool(self._stop_requested.is_set() and self._thread
                    and self._thread.is_alive())

    def _get_dependency_error(self):
        if FastMCP is None:
            return _MCP_IMPORT_ERROR
        if Starlette is None or Mount is None:
            return _STARLETTE_IMPORT_ERROR
        if uvicorn is None:
            return _UVICORN_IMPORT_ERROR
        return None

    def _run_server(self):
        try:
            asyncio.run(self._serve())
        except (Exception, SystemExit) as error:
            self._startup_error = error
            logger.exception("Spyder MCP server crashed during startup")
        finally:
            self._startup_complete.set()

    async def _serve(self):
        config = uvicorn.Config(
            app=self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            # Never let uvicorn install its own dictConfig: inside Spyder
            # stdout is redirected to the internal console and uvicorn's
            # default formatter fails to build there ("Unable to configure
            # formatter 'default'"). The package logger already exists.
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        self._uvicorn_server = server
        if self._stop_requested.is_set():
            return

        startup_task = asyncio.create_task(self._watch_startup(server))
        try:
            await server.serve()
            if not getattr(server, "started", False) and self._startup_error is None:
                self._startup_error = RuntimeError(
                    "Uvicorn exited before the MCP server became ready."
                )
        finally:
            startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await startup_task

    async def _watch_startup(self, server):
        while not getattr(server, "started", False):
            if getattr(server, "should_exit", False):
                break
            await asyncio.sleep(0.05)
        self._startup_complete.set()

    def _build_fastmcp(self):
        with _preserve_root_logging():
            mcp = FastMCP(
                "Spyder",
                instructions=(
                    "Access the current Spyder editor, project, and IPython "
                    "runtime state. Editor writes require preview + explicit "
                    "confirmation using the previewed hash and positions, and "
                    "runtime writes target one explicit or active Spyder "
                    "IPython console."
                ),
                stateless_http=True,
                json_response=True,
                streamable_http_path=self._path,
            )
        self._configure_fastmcp_settings(mcp)

        @mcp.tool()
        def get_current_file() -> dict[str, Any]:
            """Get the active Spyder editor file, full content, cursor, and selection."""
            return self._bridge.get_current_file()

        @mcp.tool()
        def get_open_files() -> list[dict[str, Any]]:
            """Get summaries of the other files currently open in Spyder."""
            return self._bridge.get_open_files()

        @mcp.tool()
        def get_project_tree() -> dict[str, Any]:
            """Get the active Spyder project root and a bounded file-tree listing."""
            return self._bridge.get_project_tree()

        @mcp.tool()
        def list_project_files(subdir: str = "", glob: str = "",
                               max_entries: int = 300) -> dict[str, Any]:
            """List files under the Spyder project root (bounded, skips caches/VCS dirs)."""
            return self._normalize_runtime_result(self._bridge.execute_project_request(
                "project.list_files", subdir=subdir, glob=glob, max_entries=max_entries,
            ))

        @mcp.tool()
        def read_project_file(path: str, start_line: int = 1, end_line: int = 0,
                              max_chars: int = 20000) -> dict[str, Any]:
            """Read one text file under the Spyder project root (relative path, optional line range)."""
            args = {"path": path, "start_line": start_line, "max_chars": max_chars}
            if end_line > 0:
                args["end_line"] = end_line
            return self._normalize_runtime_result(
                self._bridge.execute_project_request("project.read_file", **args)
            )

        @mcp.tool()
        def search_project(pattern: str, glob: str = "",
                           max_results: int = 50) -> dict[str, Any]:
            """Regex-search text files under the Spyder project root."""
            return self._normalize_runtime_result(self._bridge.execute_project_request(
                "project.search", pattern=pattern, glob=glob, max_results=max_results,
            ))

        @mcp.tool()
        def git_status() -> dict[str, Any]:
            """Short git status (with branch) of the Spyder project."""
            return self._normalize_runtime_result(
                self._bridge.execute_project_request("git.status")
            )

        @mcp.tool()
        def git_diff(path: str = "", staged: bool = False,
                     max_chars: int = 20000) -> dict[str, Any]:
            """Uncommitted (or staged) git diff of the Spyder project, optionally for one path."""
            return self._normalize_runtime_result(self._bridge.execute_project_request(
                "git.diff", path=path, staged=staged, max_chars=max_chars,
            ))

        @mcp.tool()
        def git_log(max_count: int = 10, path: str = "") -> dict[str, Any]:
            """Recent git commits of the Spyder project, optionally for one path."""
            return self._normalize_runtime_result(self._bridge.execute_project_request(
                "git.log", max_count=max_count, path=path,
            ))

        @mcp.tool()
        def get_consoles() -> dict[str, Any]:
            """List the available Spyder IPython console targets."""
            result = self._bridge.execute_runtime_request("runtime.list_shells")
            return self._normalize_runtime_result(result)

        @mcp.tool()
        def get_variables(limit: int = 12, shell_id: str = "") -> dict[str, Any]:
            """List visible variables from a Spyder IPython console."""
            result = self._bridge.execute_runtime_request(
                "runtime.list_variables",
                limit=limit,
                shell_id=shell_id,
            )
            return self._normalize_runtime_result(result)

        @mcp.tool()
        def inspect_variable(name: str, shell_id: str = "") -> dict[str, Any]:
            """Inspect one named variable from a Spyder IPython console."""
            result = self._bridge.execute_runtime_request(
                "runtime.inspect_variable",
                name=name,
                shell_id=shell_id,
            )
            variables = (result.get("payload") or {}).get("variables", [])
            normalized = self._normalize_runtime_result(result)
            normalized["variable"] = variables[0] if variables else {}
            return normalized

        @mcp.tool()
        def get_traceback(shell_id: str = "") -> dict[str, Any]:
            """Get the latest traceback or error from a Spyder IPython console."""
            result = self._bridge.execute_runtime_request(
                "runtime.get_latest_error",
                shell_id=shell_id,
            )
            return self._normalize_runtime_result(result)

        @mcp.tool()
        def get_console_output(max_chars: int = 3000,
                               shell_id: str = "") -> dict[str, Any]:
            """Get the recent visible console output from a Spyder IPython console."""
            result = self._bridge.execute_runtime_request(
                "runtime.get_console_tail",
                max_chars=max_chars,
                shell_id=shell_id,
            )
            return self._normalize_runtime_result(result)

        @mcp.tool()
        def preview_file_edit(
            code: str,
            mode: str = "insert",
            filename: str = "",
            cursor_position: int = -1,
            selection_start: int = -1,
            selection_end: int = -1,
        ) -> dict[str, Any]:
            """Preview an editor mutation (mode: insert, replace, replace_definition) without changing the document."""
            return self._bridge.preview_file_edit(
                code,
                filename=filename,
                requested_mode=mode,
                cursor_position=cursor_position,
                selection_start=selection_start,
                selection_end=selection_end,
            )

        @mcp.tool()
        def apply_file_edit(
            code: str,
            mode: str = "insert",
            filename: str = "",
            cursor_position: int = -1,
            selection_start: int = -1,
            selection_end: int = -1,
            expected_document_sha256: str = "",
            confirm: bool = False,
            save: bool = False,
        ) -> dict[str, Any]:
            """Apply one previewed editor mutation (mode: insert, replace, replace_definition) after confirmation."""
            return self._bridge.apply_file_edit(
                code,
                filename=filename,
                requested_mode=mode,
                cursor_position=cursor_position,
                selection_start=selection_start,
                selection_end=selection_end,
                expected_document_sha256=expected_document_sha256,
                confirm=confirm,
                save=save,
            )

        @mcp.tool()
        def execute_console_code(
            code: str,
            shell_id: str = "",
            hidden: bool = False,
        ) -> dict[str, Any]:
            """Submit explicit code to the active or selected Spyder IPython console."""
            result = self._bridge.execute_runtime_request(
                "runtime.execute_code",
                code=code,
                shell_id=shell_id,
                hidden=hidden,
            )
            return self._normalize_runtime_result(result)

        return mcp

    def _configure_fastmcp_settings(self, mcp):
        settings = getattr(mcp, "settings", None)
        if settings is None:
            return

        for attr, value in (
            ("host", self._host),
            ("port", self._port),
            ("streamable_http_path", self._path),
        ):
            with contextlib.suppress(Exception):
                setattr(settings, attr, value)

    def _build_asgi_app(self, mcp):
        @contextlib.asynccontextmanager
        async def lifespan(app):
            del app
            async with mcp.session_manager.run():
                yield

        return Starlette(
            routes=[Mount("/", app=mcp.streamable_http_app())],
            lifespan=lifespan,
        )

    @staticmethod
    def _normalize_runtime_result(result):
        payload = dict((result or {}).get("payload") or {})
        normalized = {
            "ok": bool((result or {}).get("ok", False)),
            "source": (result or {}).get("source", ""),
            "shell_status": (result or {}).get("shell_status", ""),
            "shell_detail": (result or {}).get("shell_detail", ""),
            "shell_id": (result or {}).get("shell_id", ""),
            "shell_label": (result or {}).get("shell_label", ""),
            "active_shell_id": (result or {}).get("active_shell_id", ""),
            "active_shell_label": (result or {}).get("active_shell_label", ""),
            "target_shell_id": (result or {}).get("target_shell_id", ""),
            "target_shell_label": (result or {}).get("target_shell_label", ""),
            "working_directory": (result or {}).get("working_directory", ""),
            "last_refreshed_at": (result or {}).get("last_refreshed_at", ""),
            "query_note": (result or {}).get("query_note", ""),
            "error": (result or {}).get("error", ""),
        }
        normalized.update(payload)
        return normalized
