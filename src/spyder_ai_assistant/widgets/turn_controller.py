"""Turn lifecycle helpers extracted from ``chat_widget.py``."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from spyder_ai_assistant.utils.runtime_bridge import (
    MAX_RUNTIME_TOOL_CALLS_PER_TURN,
    format_runtime_observation,
    parse_runtime_request,
)

logger = logging.getLogger(__name__)


@dataclass
class ChatTurnState:
    """Internal per-turn state used for runtime inspection loops."""

    session: object
    request_messages: list
    tool_calls: int = 0


class TurnController:
    """Own request dispatch and runtime continuation state."""

    def __init__(self, *, send_chat_emitter, worker_abort):
        self._send_chat_emitter = send_chat_emitter
        self._worker_abort = worker_abort
        self.context_provider = None
        self.runtime_request_executor = None
        self.runtime_status_notifier = None
        self._pending_turn = None
        self._generating = False
        self._generating_session = None

    @property
    def generating(self):
        """Return whether a response is currently in flight."""
        return self._generating

    @property
    def generating_session(self):
        """Return the session currently receiving streamed output."""
        return self._generating_session

    @property
    def pending_turn(self):
        """Return the current hidden turn state, if any."""
        return self._pending_turn

    @property
    def pending_tool_calls(self):
        """Return the current runtime-tool call count for the active turn."""
        if self._pending_turn is None:
            return 0
        return self._pending_turn.tool_calls

    def dispatch_messages(
        self,
        session,
        request_messages,
        provider,
        model,
        options,
        *,
        tool_calls=0,
    ):
        """Start one assistant turn using already prepared messages."""
        self._generating = True
        self._generating_session = session
        self._pending_turn = ChatTurnState(
            session=session,
            request_messages=list(request_messages),
            tool_calls=tool_calls,
        )
        self._send_chat_emitter(
            provider,
            model,
            list(request_messages),
            dict(options or {}),
        )
        return True

    @staticmethod
    def build_request_messages(session, system_prompt):
        """Build the full request payload for one chat session."""
        return [{"role": "system", "content": system_prompt}] + list(
            session.messages or []
        )

    def regenerate_last_turn(self, session, system_prompt):
        """Prepare one regeneration request for the active chat tab."""
        if session is None or not session.messages:
            if session:
                session.display.append_error(
                    "No conversation is available to regenerate."
                )
            return None

        if session.messages[-1].get("role") == "assistant":
            session.messages.pop()

        if not session.messages or session.messages[-1].get("role") != "user":
            session.display.append_error(
                "Regenerate needs a previous user message on this tab."
            )
            return None

        session.display.rebuild_from_messages(session.messages)
        session.touch()
        return self.build_request_messages(session, system_prompt)

    def process_response(self, full_text, session):
        """Reduce one completed response into the next UI action."""
        clean_text = self.strip_thinking(full_text)
        runtime_request = parse_runtime_request(clean_text)

        if session and runtime_request is not None:
            if self.pending_tool_calls > MAX_RUNTIME_TOOL_CALLS_PER_TURN:
                session.display.discard_assistant_message()
                return ("error", "The model kept requesting runtime inspection "
                        "after reaching the turn limit. Try another prompt or model.")
            request_messages = self.handle_runtime_request(runtime_request, session)
            if request_messages is not None:
                return ("runtime_continue", request_messages)

        if not clean_text.strip():
            return ("empty", None)

        return ("complete", clean_text)

    def handle_runtime_request(self, runtime_request, session):
        """Execute an internal runtime request and prepare the continuation."""
        session.display.discard_assistant_message()

        if self._pending_turn is None or self._pending_turn.session is not session:
            logger.warning("Missing pending turn state for runtime request")
            return None

        logger.info(
            "Intercepted runtime request from model: %s",
            runtime_request.get("tool", "runtime.unknown"),
        )

        if self._pending_turn.tool_calls >= MAX_RUNTIME_TOOL_CALLS_PER_TURN:
            return self.continue_after_runtime_observation(
                session, runtime_request,
                {"ok": False, "payload": {}, "error": (
                    "Runtime inspection limit reached for this turn. "
                    "Answer with the available information."
                )},
            )

        if not runtime_request.get("valid"):
            logger.warning(
                "Rejected malformed runtime request: %s",
                runtime_request.get("error", "unknown error"),
            )
            return self.continue_after_runtime_observation(
                session,
                runtime_request,
                {
                    "ok": False,
                    "tool": "runtime.invalid_request",
                    "source": "unavailable",
                    "shell_status": "unavailable",
                    "shell_detail": "",
                    "working_directory": "",
                    "last_refreshed_at": "",
                    "payload": {},
                    "query_note": "",
                    "error": runtime_request.get(
                        "error", "Malformed runtime request."
                    ),
                },
            )

        if self.runtime_request_executor is None:
            logger.warning(
                "Runtime request executor is unavailable for tool %s",
                runtime_request["tool"],
            )
            return self.continue_after_runtime_observation(
                session,
                runtime_request,
                {
                    "ok": False,
                    "tool": runtime_request["tool"],
                    "source": "unavailable",
                    "shell_status": "unavailable",
                    "shell_detail": "",
                    "working_directory": "",
                    "last_refreshed_at": "",
                    "payload": {},
                    "query_note": "",
                    "error": "Runtime inspection is not currently available.",
                },
            )

        if callable(self.runtime_status_notifier):
            self.runtime_status_notifier("Inspecting runtime...")

        try:
            result = self.runtime_request_executor(runtime_request)
        except Exception as error:
            logger.exception(
                "Runtime request executor crashed for tool %s",
                runtime_request["tool"],
            )
            result = {
                "ok": False,
                "tool": runtime_request["tool"],
                "source": "unavailable",
                "shell_status": "unavailable",
                "shell_detail": "",
                "working_directory": "",
                "last_refreshed_at": "",
                "payload": {},
                "query_note": "",
                "error": f"Runtime inspection failed: {error}",
            }
        logger.info(
            "Runtime request %s completed (ok=%s, source=%s)",
            runtime_request["tool"],
            result.get("ok"),
            result.get("source", ""),
        )
        return self.continue_after_runtime_observation(
            session,
            runtime_request,
            result,
        )

    def continue_after_runtime_observation(self, session, runtime_request, result):
        """Append one hidden runtime observation and continue the same turn."""
        if self._pending_turn is None or self._pending_turn.session is not session:
            return None

        # Count every continuation, including malformed/unavailable requests,
        # so a model cannot create an unbounded hidden request loop.
        self._pending_turn.tool_calls += 1
        observation = format_runtime_observation(runtime_request, result)
        logger.info(
            "Continuing chat turn after runtime observation for %s (tool call %d/%d)",
            runtime_request.get("tool", "runtime.unknown"),
            self._pending_turn.tool_calls,
            MAX_RUNTIME_TOOL_CALLS_PER_TURN,
        )
        self._pending_turn.request_messages.extend(
            [
                {
                    "role": "assistant",
                    "content": runtime_request.get("raw_text", ""),
                },
                {
                    "role": "user",
                    "content": observation,
                },
            ]
        )
        return list(self._pending_turn.request_messages)

    @staticmethod
    def strip_thinking(text):
        """Remove ``<think>`` blocks from one model response."""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return cleaned.lstrip("\n")

    def finish_turn(self):
        """Clear the active generation state."""
        self._generating = False
        self._generating_session = None
        self._pending_turn = None

    def abort_generation(self):
        """Abort the current worker request and clear turn state."""
        self._worker_abort()
        self.finish_turn()
