# Chat Workflows

This document describes the current shipped chat workflow in Spyder.

## Toolbar status

The chat toolbar exposes two lightweight context indicators:

- the current editor context label (`filename.py:line`)
- the current kernel label (`Kernel: unavailable`, `starting`, `busy`, `ready`, or `error`)

The kernel label tooltip adds:

- cwd
- last refresh time
- tracked variable count
- whether a latest error is available

This keeps runtime visibility in the UI without attaching live state to every prompt.

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
- a broader history archive for saved sessions in the current scope

Hidden runtime tool requests are not written into saved history.

The chat pane also exposes a session history browser through:

- the `History` button in the pane
- the `Chat History...` action in the pane options menu

The history browser works within the current persistence scope:

- `Project` when a Spyder project is active
- `Global` when no project is active

Each saved row shows:

- title
- last updated timestamp
- message count
- whether the session is currently open in a tab

Available actions:

- `Open` focuses an already-open session or restores a saved one into a tab
- `Duplicate` creates a new tab with a fresh session id and copied messages
- `Delete` removes the saved session from history and closes its tab if it is open

## Quick actions

The chat pane exposes one-click actions for the highest-frequency debugging flows:

- `Explain Error`
- `Fix Traceback`
- `Use Variables`
- `Use Console`
- `Regenerate`

The first four actions reuse the runtime bridge described in [runtime-inspection.md](runtime-inspection.md). They do not inject console or variable dumps directly. Instead, they steer the model to request live runtime data only when needed.

If the input box already contains text, that text is folded into the quick action prompt instead of being discarded.

## Code apply actions

Assistant code blocks expose three actions:

- `Copy`
- `Insert at cursor`
- `Replace selection`

`Insert at cursor` preserves any selected text and inserts at the active caret position. `Replace selection` replaces the current selection when one exists, and falls back to inserting at the caret when there is no selection.

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
- editor context label
- runtime status metadata

The exported message history remains user-plus-assistant only. Hidden runtime tool requests are not written into the visible conversation transcript.

## Logging expectations

Useful Phase 4 log lines include:

- `Updated runtime toolbar status: Kernel: ready`
- `Chat session scope set to ...`
- `Restored 2 chat session(s) from ...`
- `Saved 2 chat session(s) to ...`
- `Built chat history browser with 2 saved session(s)`
- `History browser selected action 'open' for session ...`
- `History browser selected action 'duplicate' for session ...`
- `History browser selected action 'delete' for session ...`
- `Reopened chat session from history: ...`
- `Duplicated chat session from history: ... -> ...`
- `Deleted chat session from history: ...`
- `Dispatching debug quick action: explain_error`
- `Intercepted runtime request from model: runtime.get_latest_error`
- `Runtime request runtime.get_latest_error completed (ok=True, source=snapshot)`
- `Applied chat code at the current cursor position`
- `Applied chat code by replacing the current editor selection`
- `Regenerating the last assistant answer for the active chat tab`
- `Exported chat session to ...`

## Manual validation checklist

1. Start Spyder in the target environment and open the AI Chat pane.
2. Confirm the runtime label reaches `Kernel: ready` and the tooltip contains cwd and refresh metadata.
3. Raise `1/0` in the active console and click `Explain Error`.
4. Click `Fix Traceback` and confirm the answer is based on the live traceback.
5. Create `values = [1, 2, 3]`, click `Use Variables`, and confirm the answer references `values`.
6. Print a visible marker, click `Use Console`, and confirm the answer references the marker.
7. Send a normal prompt, click `Regenerate`, and confirm the active tab still has one user message and one assistant answer for that turn.
8. Use the code-block apply actions in an editor and confirm `Insert at cursor` and `Replace selection` behave differently.
9. Click `History`, confirm the browser shows the current scope, reopen a saved session, duplicate another one, and delete a saved session.
10. Close and reopen Spyder with the same project open, then confirm the chat tabs and active tab restore from `.spyproject/ai-assistant/chat-sessions.json`.
11. Export the conversation and confirm the Markdown contains model, editor context, and runtime metadata.
