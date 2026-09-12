"""A bounded daemon worker pool for blocking, Qt-free tool calls.

Resolve editor/project state on the GUI thread before submitting work.
Results are queued back to that thread. Shutdown drops callbacks and queued
jobs immediately; it never waits for a blocking callable to return.
"""

from __future__ import annotations

import queue
import threading

from qtpy.QtCore import QObject, Qt, Signal, Slot

MAX_BACKGROUND_TOOL_THREADS = 2
MAX_QUEUED_TOOL_CALLS = 8


class BackgroundToolExecutor(QObject):
    """Run at most two tool calls concurrently, delivering results on Qt."""

    _sig_finished = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks = {}
        self._jobs = queue.Queue(maxsize=MAX_QUEUED_TOOL_CALLS)
        self._stopping = threading.Event()
        self._sig_finished.connect(self._deliver, Qt.QueuedConnection)
        for index in range(MAX_BACKGROUND_TOOL_THREADS):
            threading.Thread(
                target=self._run_jobs,
                args=(self._jobs, self._stopping, self._sig_finished),
                name=f"SpyderAITool-{index + 1}",
                daemon=True,
            ).start()

    def submit(self, work, on_done):
        """Run work() and call on_done(output, error) once on Qt."""
        if self._stopping.is_set():
            raise RuntimeError("Background tool executor is shut down.")
        token = object()
        self._callbacks[token] = on_done
        try:
            self._jobs.put_nowait((token, work))
        except queue.Full:
            self._callbacks.pop(token)
            raise RuntimeError("Background tools are busy. Try again shortly.") from None
        return token

    @staticmethod
    def _run_jobs(jobs, stopping, finished):
        while not stopping.is_set():
            try:
                token, work = jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if stopping.is_set():
                    continue
                output, error = None, None
                try:
                    output = work()
                except Exception as exception:
                    error = exception
                if not stopping.is_set():
                    try:
                        finished.emit(token, output, error)
                    except RuntimeError:
                        pass  # Qt parent was deleted during teardown.
            finally:
                jobs.task_done()

    @Slot(object, object, object)
    def _deliver(self, token, output, error):
        on_done = self._callbacks.pop(token, None)
        if on_done is not None:
            on_done(output, error)

    def shutdown(self):
        """Discard waiting work without blocking the IDE on running I/O."""
        self._stopping.set()
        self._callbacks.clear()
        while True:
            try:
                self._jobs.get_nowait()
                self._jobs.task_done()
            except queue.Empty:
                break
