# Runtime Inspection

The chat workflow keeps live kernel state out of ordinary prompts by default.

Instead of attaching console dumps and variable lists to every request, the chat system prompt advertises a small read-only runtime inspection protocol. When a question depends on the current Spyder session, the model can request runtime data from the active IPython console and continue the same turn with that observation.

## What the chat can inspect

- current runtime status and freshness
- available console targets and the current runtime target
- latest extracted traceback or error block
- recent visible console output
- current variable list
- targeted inspection of one or more named variables
- richer runtime summaries for arrays, images, DataFrames, Series, and bounded nested containers

## What it still cannot do

- execute code in the kernel
- mutate variables
- attach the full namespace automatically to every prompt
- run arbitrary terminal or shell commands through the bridge

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

- `runtime.list_shells`
- `runtime.status`
- `runtime.get_latest_error`
- `runtime.get_console_tail`
- `runtime.list_variables`
- `runtime.inspect_variable`
- `runtime.inspect_variables`

The frontend intercepts that request, queries the active shellwidget, formats the result as a hidden observation, and lets the same chat turn continue. The visible conversation only shows the user's message and the final assistant answer.

When more than one IPython console is open, requests can explicitly target a shell id returned by `runtime.list_shells`, or they can use the console pinned in the chat toolbar. This keeps multi-console debugging deterministic instead of silently following whichever console happened to be active.

## Phase 4 chat workflow on top of the bridge

The chat pane now adds a few explicit runtime-aware affordances on top of the same bridge:

- a toolbar label that shows the active kernel state (`unavailable`, `starting`, `busy`, `ready`, or `error`)
- a runtime target selector that can follow the active console or pin inspection to another open console
- a runtime tooltip with cwd, refresh time, variable count, and latest-error availability
- quick actions for `Explain Error`, `Fix Traceback`, `Use Variables`, and `Use Console`
- `Regenerate`, which reruns the last user turn on the active tab without duplicating the old assistant answer

These actions still do not inject runtime dumps into the prompt. They only steer the model toward the existing inspection protocol when the question depends on live state.

## Logging expectations

Healthy logs and validation artifacts for the runtime bridge should contain lines like:

- `Runtime context service bound to IPython Console plugin`
- `Runtime context tracking shell ...`
- `Runtime context seeded namespace view settings for ...`
- `Executing runtime request: tool=runtime.inspect_variable requested_shell=... selected_shell=... active_shell=...`
- `Intercepted runtime request from model: runtime.inspect_variable`
- `Runtime request runtime.inspect_variable refreshed live namespace state for ...`
- `Runtime request completed: tool=runtime.inspect_variable ok=True source=live shell=... active=... target=... error=`

The tracked Phase 10 live harness also prints its key checkpoints into the run log:

- DataFrame columns and preview path
- array shape, dtype, and numeric range
- traceback exception type and frame count
- shell-target listing with active vs pinned target flags
- the final chat answer returned against the pinned console

If a model ignores the protocol, the logs will usually show a normal assistant response with no runtime request interception. That is a model-compatibility issue, not a shell integration failure.

If a model returns an empty answer instead of following the protocol, the chat pane now reports `Empty response` and logs a warning instead of silently saving a blank assistant message.

## Manual validation checklist

1. Start Spyder in the environment where the plugin is installed.
2. Open the AI Chat pane and confirm the runtime label progresses from `Kernel: unavailable` to `Kernel: ready`.
3. In IPython, trigger a traceback such as `1/0`, then click `Explain Error`. Confirm the logs show `runtime.get_latest_error`.
4. Click `Fix Traceback`. Confirm the answer is based on the live traceback rather than only file context.
5. Create a variable such as `values = [1, 2, 3]`, then click `Use Variables` with a short prompt like "focus on values". Confirm the logs show `runtime.list_variables` or `runtime.inspect_variable`.
6. Print a marker such as `print("PHASE4_CONSOLE_MARKER")`, then click `Use Console`. Confirm the answer references the printed marker and the logs show `runtime.get_console_tail`.
7. Send a normal prompt, then click `Regenerate`. Confirm the last assistant answer is replaced rather than duplicated.
8. Export the active conversation and confirm the Markdown includes model, editor context, runtime status, and runtime metadata.
9. Open a second console, create a different variable, pin the chat runtime target to that console, then switch the active console back. Confirm the runtime tooltip shows both the active console and the pinned inspection target.
10. Inspect an array such as `np.arange(6).reshape(2, 3)` and confirm the answer or validation artifact includes the shape, dtype, and range `0..5`.
11. Raise `1/0` in the pinned console and confirm the traceback summary includes both `ZeroDivisionError` and at least one parsed frame.
12. With a busy kernel, repeat a variable question and confirm the logs or JSON artifact show a cached fallback instead of a crash.

## Model compatibility note

Runtime inspection depends on the selected chat model following the structured request protocol.

In local validation for this phase:

- Qwen-based chat models followed the protocol correctly
- `gpt-oss:20b` returned an empty answer for a live runtime question in Spyder, which is now surfaced in the UI as `Empty response` with a warning in the logs instead of a blank assistant turn

This does not break the plugin, but it does reduce the usefulness of live debugging. If runtime inspection does not trigger when it should, test with a stronger instruction-following model first.
