"""Run blocking, Qt-free tool calls off the GUI thread.

Project and git tools are ordinary filesystem and subprocess work: a
project-wide regex search walks up to ``MAX_SEARCH_FILES`` files, and a
``git diff`` on a large repository can sit for seconds. Called straight from
a Qt slot -- which is what the chat turn loop used to do -- that work freezes
the whole IDE, including the transcript the user is reading and the editor
they would switch to while waiting.

This wraps Spyder's own ``WorkerManager`` so callers stay declarative: hand
over a zero-argument callable plus a completion callback, and the callback
runs back on the GUI thread once the worker finishes.

Only submit work that touches no Qt object and no Spyder widget. Anything
that reads editor, console or project state has to be resolved on the GUI
thread first and passed in as plain data; ``ProjectToolsService`` splits
itself along exactly that line (``prepare_request`` then ``run_prepared``).
"""

from __future__ import annotations

import logging

from qtpy.QtCore import QObject

from spyder.utils.workers import WorkerManager

logger = logging.getLogger(__name__)

# Two concurrent tool calls is already generous: a chat turn runs one tool at
# a time, and the MCP server does its own project work on its own thread.
MAX_BACKGROUND_TOOL_THREADS = 2


class BackgroundToolExecutor(QObject):
    """Submit blocking tool calls to a small worker-thread pool."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker_manager = WorkerManager(
            parent=self,
            max_threads=MAX_BACKGROUND_TOOL_THREADS,
        )
        # Worker object -> completion callback. Keyed by the worker itself
        # rather than by ``id(worker)`` so a recycled id can never deliver a
        # result to the wrong caller, and so the mapping keeps the worker
        # alive until it has reported back.
        self._callbacks = {}

    def submit(self, work, on_done):
        """Run ``work()`` on a worker thread, reporting back on the GUI thread.

        ``work`` takes no arguments and must not touch Qt or Spyder state.
        ``on_done(output, error)`` is called on the GUI thread exactly once:
        ``output`` is whatever ``work`` returned, and ``error`` is the
        exception it raised, or None. Returns the worker so a caller can tell
        two submissions apart in a test.
        """
        worker = self._worker_manager.create_python_worker(work)
        self._callbacks[worker] = on_done
        # Connected from the GUI thread to this GUI-thread QObject, so Qt
        # queues the worker thread's emission back onto the event loop
        # instead of running the callback on the worker.
        worker.sig_finished.connect(self._deliver)
        worker.start()
        logger.debug(
            "Submitted background tool work (%d in flight)",
            len(self._callbacks),
        )
        return worker

    def _deliver(self, worker, output, error):
        """Hand one finished worker's outcome to the caller that queued it."""
        on_done = self._callbacks.pop(worker, None)
        if on_done is None:
            # Already delivered, or dropped by shutdown().
            logger.debug("Ignoring a background result with no waiting caller")
            return
        on_done(output, error)

    def shutdown(self):
        """Stop every worker and drop the callbacks still waiting.

        Called from plugin teardown: a result delivered after the chat
        widgets are gone would touch deleted Qt objects.
        """
        pending = len(self._callbacks)
        self._callbacks.clear()
        self._worker_manager.terminate_all()
        if pending:
            logger.info(
                "Dropped %d in-flight background tool call(s) during shutdown",
                pending,
            )
