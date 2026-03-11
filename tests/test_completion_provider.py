"""Unit tests for completion scheduling and cleanup helpers."""

import unittest
from types import SimpleNamespace

from spyder_ai_chat.completion_provider import (
    AIChatCompletionProvider,
    _LatestOnlyCompletionQueue,
    _QueuedCompletionRequest,
    _clean_completion,
)


class _FakeTimer:
    """Minimal timer stub used to observe debounce restarts."""

    def __init__(self):
        self.started_with = []
        self.stopped = False

    def start(self, value):
        self.started_with.append(value)

    def stop(self):
        self.stopped = True


class _FakeWorkerSignal:
    """Signal stub that records emitted payloads."""

    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _FakeWorker:
    """Minimal worker stub for provider dispatch tests."""

    def __init__(self):
        self.sig_perform_completion = _FakeWorkerSignal()
        self._thread = SimpleNamespace(isRunning=lambda: False)
        self.start_called = 0
        self.updated_host = []

    def start(self):
        self.start_called += 1

    def update_host(self, host):
        self.updated_host.append(host)


class CompletionQueueTests(unittest.TestCase):
    """Cover the latest-only request queue helper."""

    def test_replacing_pending_slots_returns_dropped_request(self):
        queue = _LatestOnlyCompletionQueue()
        dropped = queue.replace_debounced(
            _QueuedCompletionRequest(req={"file": "a.py"}, req_id=1)
        )
        self.assertIsNone(dropped)

        dropped = queue.replace_debounced(
            _QueuedCompletionRequest(req={"file": "b.py"}, req_id=2)
        )
        self.assertEqual(dropped.req_id, 1)

        queue.start_active(10)
        dropped = queue.replace_queued(
            _QueuedCompletionRequest(req={"file": "c.py"}, req_id=3)
        )
        self.assertIsNone(dropped)

        dropped = queue.replace_queued(
            _QueuedCompletionRequest(req={"file": "d.py"}, req_id=4)
        )
        self.assertEqual(dropped.req_id, 3)

    def test_clear_pending_returns_ids_and_preserves_active(self):
        queue = _LatestOnlyCompletionQueue()
        queue.replace_debounced(
            _QueuedCompletionRequest(req={"file": "a.py"}, req_id=11)
        )
        queue.replace_queued(
            _QueuedCompletionRequest(req={"file": "b.py"}, req_id=12)
        )
        queue.start_active(99)

        self.assertEqual(queue.clear_pending(), [11, 12])
        self.assertEqual(queue.active_req_id, 99)


class CompletionCleanupTests(unittest.TestCase):
    """Cover completion text cleanup behavior."""

    def test_clean_completion_strips_markdown_prefix_and_suffix_echo(self):
        prefix = "def add(a, b):\n    "
        suffix = "\n    return a + b"
        raw = "```python\n" + prefix + "result = a + b\nreturn a + b\n```"
        cleaned = _clean_completion(raw, prefix, suffix)
        self.assertEqual(cleaned, "result = a + b")

    def test_looks_offline_matches_connection_errors(self):
        self.assertTrue(
            AIChatCompletionProvider._looks_offline("Connection refused")
        )
        self.assertTrue(
            AIChatCompletionProvider._looks_offline("request timed out")
        )
        self.assertFalse(
            AIChatCompletionProvider._looks_offline("model not found")
        )


class ProviderSchedulingTests(unittest.TestCase):
    """Exercise provider methods with lightweight fakes."""

    def _make_provider_stub(self, enabled=True):
        responses = []
        status_updates = []
        provider = SimpleNamespace(
            _request_queue=_LatestOnlyCompletionQueue(),
            _latest_req_id=0,
            _debounce_timer=_FakeTimer(),
            _file_contents={},
            _req_filename={},
            _worker=_FakeWorker(),
            COMPLETION_PROVIDER_NAME="ai_chat",
            _started=True,
        )

        conf = {
            "completions_enabled": enabled,
            "debounce_ms": 300,
            "completion_model": "foo/bar:baz",
            "completion_temperature": 0.15,
            "completion_max_tokens": 64,
        }
        provider.get_conf = conf.get
        provider._emit_empty_response = responses.append
        provider._update_status = status_updates.append
        provider._set_ready_status = lambda: status_updates.append("AI: ready")
        provider.responses = responses
        provider.status_updates = status_updates
        return provider

    def test_disabled_completion_answers_immediately(self):
        provider = self._make_provider_stub(enabled=False)

        AIChatCompletionProvider._handle_completion_request(
            provider, {"file": "a.py", "offset": 0}, 7
        )

        self.assertEqual(provider.responses, [7])
        self.assertEqual(provider._debounce_timer.started_with, [])

    def test_new_debounced_request_replaces_previous_one(self):
        provider = self._make_provider_stub(enabled=True)

        AIChatCompletionProvider._handle_completion_request(
            provider, {"file": "a.py", "offset": 1}, 10
        )
        AIChatCompletionProvider._handle_completion_request(
            provider, {"file": "a.py", "offset": 2}, 11
        )

        self.assertEqual(provider.responses, [10])
        self.assertEqual(provider._latest_req_id, 11)
        self.assertEqual(provider._debounce_timer.started_with, [300, 300])

    def test_debounce_queues_only_latest_behind_active_request(self):
        provider = self._make_provider_stub(enabled=True)
        provider._request_queue.start_active(42)
        provider._request_queue.replace_debounced(
            _QueuedCompletionRequest(req={"file": "a.py", "offset": 1}, req_id=1)
        )
        AIChatCompletionProvider._debounce_fire(provider)
        self.assertEqual(provider.responses, [])

        provider._request_queue.replace_debounced(
            _QueuedCompletionRequest(req={"file": "a.py", "offset": 2}, req_id=2)
        )
        AIChatCompletionProvider._debounce_fire(provider)
        self.assertEqual(provider.responses, [1])

    def test_dispatch_without_tracked_file_answers_immediately(self):
        provider = self._make_provider_stub(enabled=True)

        AIChatCompletionProvider._dispatch_request(
            provider,
            _QueuedCompletionRequest(
                req={"file": "missing.py", "offset": 5},
                req_id=22,
            ),
        )

        self.assertEqual(provider.responses, [22])
        self.assertEqual(provider._worker.sig_perform_completion.calls, [])


if __name__ == "__main__":
    unittest.main()
