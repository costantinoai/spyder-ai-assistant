# Chat Workflows

This document describes the current shipped chat workflow in Spyder.

## Toolbar status

The chat toolbar exposes two lightweight context indicators:

- the current editor context label (`filename.py:line`)
- the current kernel label (`Kernel: unavailable`, `starting`, `busy`, `ready`, or `error`)
- the current runtime target selector (`Follow Active Console` or one pinned console)

The kernel label tooltip adds:

- active console label
- current inspection target label
- cwd
- last refresh time
- tracked variable count
- whether a latest error is available

This keeps runtime visibility in the UI without attaching live state to every prompt.

When multiple IPython consoles are open, the runtime selector lets the user pin
debugging work to one console without changing the active editor or active
console tab elsewhere in Spyder.

The toolbar also exposes the per-tab chat mode selector. Each tab can choose
its own working mode:

- `Coding`
- `Debugging`
- `Review`
- `Data Analysis`
- `Explanation`
- `Documentation`

The selector is shared in the toolbar, but the chosen mode is stored on the
active chat session rather than globally.

Below the input, the visible controls are intentionally compact:

- `Debug` menu for runtime-aware quick actions
- `Regenerate`
- `Sessions`
- `Settings`
- `Stop` / `Send`

This keeps the common actions one click away without filling the pane with a
large row of equally prominent buttons. The `Sessions` button opens history on
click and exposes lower-frequency session actions from its menu. The
`Settings` button opens assistant-wide settings on click and exposes
assistant settings, tab overrides, provider profiles, and model refresh from
its menu.

The model selector is provider-aware. The same dropdown can list:

- local Ollama chat models
- models discovered from multiple named OpenAI-compatible profiles

Each entry keeps provider metadata in the tooltip so it is clear where the
next request will go before you send it. If one compatible profile fails,
working profiles stay usable and the status label reports the issue count
without collapsing the entire selector to an error state.

## Prompt presets

Prompt presets are built-in system-prompt overlays for the active chat tab.

Behavior:

- the base prompt from Preferences still applies to every chat request
- the active tab's preset adds mode-specific instructions on top of that base
- runtime bridge instructions remain separate and always apply consistently
- switching tabs updates the selector to the restored preset for that tab
- duplicating or reopening a saved session preserves its preset

The shipped presets are:

- `Coding` for implementation, refactoring, and code changes
- `Debugging` for root-cause analysis and concrete fixes
- `Review` for bugs, regressions, missing tests, and maintainability issues
- `Data Analysis` for arrays, tables, plots, and scientific/debugging workflows
- `Explanation` for teaching and understanding code
- `Documentation` for docstrings, usage notes, and polished written guidance

## Per-tab inference settings

The active chat tab also owns its own optional inference overrides.

Controls:

- `Settings > Tab Overrides...` in the chat pane
- `Tab Overrides...` action in the pane options menu

Available overrides:

- temperature
- max tokens

Behavior:

- overrides apply only to the active chat session
- unchecked values fall back to the global Preferences defaults
- switching tabs updates the `Settings` button tooltip to match the active tab
- tabs with active overrides show `Settings*`
- reopening or duplicating a saved session preserves its saved overrides
- resetting a tab back to global defaults clears the saved override fields

Assistant-wide settings stay beside those overrides instead of living in
Spyder Preferences. `Settings > Assistant Settings...` contains:

- recognized chat and completion model dropdowns
- Ollama host
- completion enable/disable
- global temperature/token defaults
- ghost-text shortcuts
- base system prompt and editor action prompt templates

## Provider-aware chat transport

Chat transport is intentionally broader than the completion transport.

Behavior:

- chat can target Ollama or an OpenAI-compatible endpoint
- multiple named OpenAI-compatible profiles can coexist
- the provider switch happens in the same dock widget with no restart required
- provider-specific connection failures identify the failing endpoint
- provider profiles are managed from `Settings > Provider Profiles...`
- saved chat/completion model defaults are chosen from the recognized model
  list returned by the local Ollama endpoint and enabled provider profiles
- saving the profile dialog migrates any legacy single-endpoint config into the
  profile store so removed profiles stay removed
- completions can use the configured completion provider/profile and model

## Session persistence and history

Chat sessions persist automatically.

Storage rules:

- if a Spyder project is active, sessions are saved to `.spyproject/ai-assistant/chat-sessions.json`
- if no project is active, sessions are saved to a global file in Spyder's config directory

The persisted payload keeps:

- tab order
- active tab index
- tab titles
- visible user/assistant message history
- the selected prompt preset per tab
- per-tab temperature and max-token overrides
- a broader history archive for saved sessions in the current scope

Hidden runtime tool requests are not written into saved history.

The chat pane also exposes a session history browser through:

- the `Sessions` button in the pane
- the `Chat History...` action in the pane options menu

The history browser works within the current persistence scope:

- `Project` when a Spyder project is active
- `Global` when no project is active

Each saved row shows:

- title
- prompt mode
- last updated timestamp
- message count
- whether the session is currently open in a tab

The browser also supports:

- free-text search across title, preview, and prompt mode
- status filtering for `All sessions`, `Open tabs`, or `Saved only`
- sorting by update time, title, or message count

Available actions:

- `Open` focuses an already-open session or restores a saved one into a tab
- `Duplicate` creates a new tab with a fresh session id and copied messages
- `Delete` removes the saved session from history and closes its tab if it is open

## Quick actions

The chat pane exposes a compact `Debug` menu for the highest-frequency
runtime-aware workflows:

- `Explain Error`
- `Fix Traceback`
- `Use Variables`
- `Use Console`

`Regenerate` remains visible beside the debug menu because it is used often
but does not require extra runtime inspection scaffolding.

The first four actions reuse the runtime bridge described in [runtime-inspection.md](runtime-inspection.md). They do not inject console or variable dumps directly. Instead, they steer the model to request live runtime data only when needed.

If the input box already contains text, that text is folded into the quick action prompt instead of being discarded.

These runtime-aware actions follow the pinned runtime target when one is
selected. Otherwise they follow the active Spyder IPython console.

## Completion intelligence

Inline completions remain separate from the chat worker, but the shipped
completion workflow is now richer than plain prefix/suffix prompting.

Behavior:

- small relevant snippets from other tracked open files can be attached when
  the current completion target clearly relates to them
- repeated requests on the same visible target can ask for and cycle through
  alternative candidates locally
- the completion status tooltip reports neighbor-context and cycling counters

## Code apply actions

Assistant code blocks now expose two actions:

- `Copy`
- `Apply...`

`Apply...` opens a preview dialog that:

- defaults to `Replace selection` when an editor selection exists
- otherwise defaults to `Insert at cursor`
- shows a unified diff preview before any mutation
- lets the user cancel safely
- groups the final edit into one undo step

This is intentionally safer than mutating the editor immediately from the chat transcript.

## Exchange deletion

The active chat tab exposes two entry points for exchange deletion:

- `Delete Exchange...` action in the `Sessions` menu
- `Delete Exchange...` action in the pane options menu

Behavior:

- the delete browser lists visible exchanges in the active tab only
- each row previews one user turn plus its assistant answer when present
- deleting an exchange updates the authoritative session history first
- the display is rebuilt from the updated session history after deletion
- exports and persisted session files then reflect the same reduced transcript
- regenerate continues to operate on the remaining last user turn

## Regenerate

`Regenerate` operates on the active chat tab only.

Behavior:

- remove the trailing assistant answer from the active session
- rebuild the visible conversation from authoritative session history
- rerun the last user turn

This avoids leaving stale duplicate answers in the tab.

## Export

Chat export now writes Markdown with:

- timestamp
- model
- chat mode
- resolved per-tab temperature and max-token settings
- editor context label
- runtime status metadata

The exported message history remains user-plus-assistant only. Hidden runtime tool requests are not written into the visible conversation transcript.

## Logging expectations

Useful log lines include:

- `Updated runtime toolbar status: Kernel: ready`
- `Chat session scope set to ...`
- `Restored 2 chat session(s) from ...`
- `Saved 2 chat session(s) to ...`
- `Chat prompt preset set to Debugging for session ...`
- `Chat prompt preset set to Documentation for session ...`
- `Updated chat settings for session ...: temperature=0.2 (tab override), max_tokens=128 (tab override)`
- `Chat worker provider settings updated: ollama=..., profile_count=...`
- `Chat worker discovered 4 chat model(s)`
- `Provider diagnostic: id=openai_compatible:alpha kind=openai_compatible label=Alpha Lab status=ready models=1 endpoint=http://... message=`
- `Provider profile selection fell back from beta to alpha`
- `Building chat system prompt with preset debugging for session ...`
- `Built chat history browser with 2 saved session(s)`
- `Opened chat history browser for Project scope`
- `Built exchange delete browser with 3 exchange(s) for session ...`
- `Opened chat code apply preview for ...`
- `Accepted chat code apply preview in insert mode`
- `Accepted chat code apply preview in replace mode`
- `Cancelled chat code apply preview`
- `History browser selected action 'open' for session ...`
- `History browser selected action 'duplicate' for session ...`
- `History browser selected action 'delete' for session ...`
- `Opened exchange delete browser for session ...`
- `Deleted exchange 2 from session ...`
- `Reopened chat session from history: ...`
- `Duplicated chat session from history: ... -> ...`
- `Deleted chat session from history: ...`
- `Dispatching debug quick action: explain_error`
- `Intercepted runtime request from model: runtime.get_latest_error`
- `Runtime request completed: tool=runtime.get_latest_error ok=True source=snapshot shell=... active=... target=... error=`
- `Applied chat code at the current cursor position`
- `Applied chat code by replacing the current editor selection`
- `Regenerating the last assistant answer for the active chat tab`
- `Dispatching chat request for session ... via ollama/... with options {'temperature': 0.2, 'num_predict': 128}`
- `Exported chat session to ...`

## Manual validation checklist

1. Start Spyder in the target environment and open the AI Chat pane.
2. Confirm the runtime label reaches `Kernel: ready` and the tooltip contains cwd and refresh metadata.
3. Raise `1/0` in the active console and click `Explain Error`.
4. Click `Fix Traceback` and confirm the answer is based on the live traceback.
5. Create `values = [1, 2, 3]`, click `Use Variables`, and confirm the answer references `values`.
6. Print a visible marker, click `Use Console`, and confirm the answer references the marker.
7. Send a normal prompt, click `Regenerate`, and confirm the active tab still has one user message and one assistant answer for that turn.
8. Use `Apply...` from a code block in an editor. Confirm the preview dialog opens, shows a diff, and that cancel leaves the editor untouched.
9. Reopen `Apply...` with a selection active, choose `Replace selection`, apply it, then confirm one undo restores the previous text exactly.
10. Set one tab to `Debugging`, open a second tab, set it to `Documentation`, then switch between tabs and confirm the toolbar selector follows the active tab.
11. Open `Settings` on one tab, set a low temperature and low max-token override, then confirm the button changes to `Settings*` and its tooltip shows the overridden values.
12. Open `Settings` on another tab, set custom values, then click `Use Global Defaults` and confirm the button returns to `Settings`.
13. Open a second console, pin the runtime target to it, and confirm `Debug > Explain Error` / `Debug > Use Variables` follow the pinned console rather than the active one.
12. Click `History`, confirm the browser shows the current scope, reopen a saved session, duplicate another one, and delete a saved session.
13. Open `Delete Turn`, remove one middle exchange from the active tab, and confirm the visible conversation rebuilds without that exchange.
14. Click `Regenerate` after deleting a middle exchange and confirm the remaining last user turn still reruns correctly.
15. Close and reopen Spyder with the same project open, then confirm the chat tabs, active tab, prompt presets, per-tab overrides, and deleted exchange state restore from `.spyproject/ai-assistant/chat-sessions.json`.
16. Export the conversation and confirm the Markdown contains model, chat mode, per-tab settings, editor context, and runtime metadata without the deleted exchange.
17. Configure an OpenAI-compatible base URL in Preferences, refresh models, select the compatible entry from the toolbar, and confirm the next chat answer comes from that endpoint.
