"""Turn lifecycle helpers extracted from ``chat_widget.py``."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import partial

from spyder_ai_assistant.utils.runtime_bridge import (
    MAX_RUNTIME_TOOL_CALLS_PER_TURN,
    format_runtime_observation,
    parse_runtime_request,
)
from spyder_ai_assistant.utils.tool_protocol import describe_tool_activity

logger = logging.getLogger(__name__)


# Returned by handle_runtime_request when the tool call was handed to the
# executor and may finish on a worker thread. The turn stays open and
# resumes from the executor's callback instead of from that return value.
PENDING_RUNTIME_REQUEST = object()


@dataclass
class ChatTurnState:
    """Internal per-turn state used for runtime inspection loops."""

    session: object
    request_messages: list
    tool_calls: int = 0
    # True while a tool call is in flight. The turn must keep reporting
    # itself as generating throughout, or the UI would re-enable Send and
    # let a second turn interleave with this one.
    awaiting_tool: bool = False


class TurnController:
    """Own request dispatch and runtime continuation state."""

    def __init__(self, *, send_chat_emitter, worker_abort):
        self._send_chat_emitter = send_chat_emitter
        self._worker_abort = worker_abort
        self.context_provider = None
        # Called as executor(request, on_result). The executor may deliver
        # its result later, from the GUI thread, once a worker finishes.
        self.runtime_request_executor = None
        self.runtime_status_notifier = None
        # Called as continuation(session, request_messages, tool_calls) to
        # resume a turn whose tool call has reported back.
        self.runtime_continuation = None
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

    @property
    def awaiting_tool(self):
        """Return whether the active turn is waiting on a tool call."""
        if self._pending_turn is None:
            return False
        return self._pending_turn.awaiting_tool

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
            if request_messages is PENDING_RUNTIME_REQUEST:
                return ("runtime_pending", None)
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
                self._unavailable_result(
                    "runtime.invalid_request",
                    runtime_request.get("error", "Malformed runtime request."),
                ),
            )

        if self.runtime_request_executor is None:
            logger.warning(
                "Runtime request executor is unavailable for tool %s",
                runtime_request["tool"],
            )
            return self.continue_after_runtime_observation(
                session,
                runtime_request,
                self._unavailable_result(
                    runtime_request["tool"],
                    "Runtime inspection is not currently available.",
                ),
            )

        if callable(self.runtime_status_notifier):
            self.runtime_status_notifier(
                describe_tool_activity(runtime_request["tool"])
            )

        # The executor decides which thread does the work: project and git
        # tools go to a worker, runtime inspection stays inline. Either way
        # the turn resumes from the callback below, so this returns a
        # sentinel rather than the next request payload. The turn stays
        # marked as generating until then.
        turn = self._pending_turn
        turn.awaiting_tool = True
        try:
            self.runtime_request_executor(
                runtime_request,
                partial(self._continue_after_tool_result, turn, runtime_request),
            )
        except Exception as error:
            logger.exception(
                "Runtime request executor crashed for tool %s",
                runtime_request["tool"],
            )
            if self._pending_turn is not turn or not turn.awaiting_tool:
                return PENDING_RUNTIME_REQUEST
            turn.awaiting_tool = False
            return self.continue_after_runtime_observation(
                session,
                runtime_request,
                self._unavailable_result(
                    runtime_request["tool"],
                    f"Runtime inspection failed: {error}",
                ),
            )
        return PENDING_RUNTIME_REQUEST

    @staticmethod
    def _unavailable_result(tool, message):
        """Return the observation envelope for a tool call that never ran.

        The observation formatter expects every runtime metadata field, so
        the "could not run" shape is built in one place rather than being
        spelled out at each rejection site.
        """
        return {
            "ok": False,
            "tool": tool,
            "source": "unavailable",
            "shell_status": "unavailable",
            "shell_detail": "",
            "working_directory": "",
            "last_refreshed_at": "",
            "payload": {},
            "query_note": "",
            "error": message,
        }

    def _continue_after_tool_result(self, turn, runtime_request, result):
        """Feed one tool result back into the turn that asked for it.

        Runs on the GUI thread. The turn may already be over by now -- the
        user pressed Stop, or the tabs were cleared by a history restore --
        in which case the observation is dropped instead of resurrecting a
        finished turn.
        """
        if self._pending_turn is not turn or not turn.awaiting_tool:
            logger.info(
                "Dropping the %s observation: its chat turn is no longer active",
                runtime_request.get("tool", "runtime.unknown"),
            )
            return

        session = turn.session
        turn.awaiting_tool = False
        logger.info(
            "Runtime request %s completed (ok=%s, source=%s)",
            runtime_request.get("tool", "runtime.unknown"),
            result.get("ok"),
            result.get("source", ""),
        )
        request_messages = self.continue_after_runtime_observation(
            session,
            runtime_request,
            result,
        )
        if request_messages is None:
            return
        if not callable(self.runtime_continuation):
            logger.error(
                "No turn continuation is wired, so the chat turn cannot resume"
            )
            return
        self.runtime_continuation(
            session,
            request_messages,
            self._pending_turn.tool_calls,
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
