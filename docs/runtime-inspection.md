# Runtime Inspection

The Phase 2 chat workflow keeps live kernel state out of ordinary prompts by default.

Instead of attaching console dumps and variable lists to every request, the chat system prompt advertises a small read-only runtime inspection protocol. When a question depends on the current Spyder session, the model can request runtime data from the active IPython console and continue the same turn with that observation.

## What the chat can inspect

- current runtime status and freshness
- latest extracted traceback or error block
- recent visible console output
- current variable list
- targeted inspection of one or more named variables

## What it cannot do in Phase 2

- execute code in the kernel
- mutate variables
- attach the full namespace automatically to every prompt
- inspect runtime state from a different console than the active one

## Design intent

The point of this design is to make debugging and exploratory data work much more useful without burning prompt budget on every normal coding question.

Ordinary chat should stay:

- lean
- file-focused
- predictable in token usage

Runtime state should only enter the conversation when the user asks about:

- the current error
- the latest console output
- current variable values
- why the live session is behaving a certain way

## Runtime request protocol

The system prompt tells the model to emit one request block as the entire assistant message when it needs runtime data:

```xml
<spyder-runtime-request>
{"tool":"runtime.inspect_variable","args":{"name":"df"}}
</spyder-runtime-request>
```

Supported read-only tools:

- `runtime.status`
- `runtime.get_latest_error`
- `runtime.get_console_tail`
- `runtime.list_variables`
- `runtime.inspect_variable`
- `runtime.inspect_variables`

The frontend intercepts that request, queries the active shellwidget, formats the result as a hidden observation, and lets the same chat turn continue. The visible conversation only shows the user's message and the final assistant answer.

## Logging expectations

Healthy logs for the runtime bridge should contain lines like:

- `Runtime context service bound to IPython Console plugin`
- `Runtime context tracking shell ...`
- `Runtime context seeded namespace view settings for ...`
- `Executing runtime request: runtime.inspect_variable`
- `Intercepted runtime request from model: runtime.inspect_variable`
- `Runtime request runtime.inspect_variable refreshed live namespace state for ...`
- `Runtime request runtime.inspect_variable completed (ok=True, source=live)`

If a model ignores the protocol, the logs will usually show a normal assistant response with no runtime request interception. That is a model-compatibility issue, not a shell integration failure.

If a model returns an empty answer instead of following the protocol, the chat pane now reports `Empty response` and logs a warning instead of silently saving a blank assistant message.

## Manual validation checklist

1. Start Spyder in the environment where the editable plugin is installed.
2. Open the AI Chat pane and confirm the plugin loads without registration errors.
3. In IPython, create one or two variables and trigger a traceback such as `1/0`.
4. Ask the chat a file-only question. Confirm it answers without runtime inspection logs.
5. Ask the chat about the current error. Confirm the logs show a runtime request and the answer references the live traceback.
6. Ask about a specific live variable. Confirm the logs show `runtime.inspect_variable` or `runtime.list_variables`.
7. Switch to another console, create a different variable, and ask again. Confirm the runtime answer follows the active console.
8. With a busy kernel, repeat a variable question and confirm the logs show a cached fallback instead of a crash.

## Model compatibility note

Runtime inspection depends on the selected chat model following the structured request protocol.

In local validation for this phase:

- Qwen-based chat models followed the protocol correctly
- `gpt-oss:20b` returned an empty answer for a live runtime question in Spyder, which is now surfaced in the UI as `Empty response` with a warning in the logs instead of a blank assistant turn

This does not break the plugin, but it does reduce the usefulness of live debugging. If runtime inspection does not trigger when it should, test with a stronger instruction-following model first.
