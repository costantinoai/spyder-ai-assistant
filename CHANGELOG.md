# Changelog

## 0.8.0 - 2026-09-12

### Added

- **Only suggest when I ask**, a new option under Completions. With it on,
  nothing is suggested as you type: no idle suggestion, no follow-up after
  accepting one, and no answer to the editor's own automatic completion
  requests. `Ctrl+Shift+Space` still asks for a suggestion, and is then the
  only thing that does. The two ghost-text delays are greyed out while the
  mode is on, since neither timer runs (GitHub issue #4)
- an empty chat tab now says what the assistant can see and offers three
  starter prompts. Clicking one fills the input instead of sending it, so
  it can be edited first
- the first provider problem is shown in the chat pane itself rather than
  only in the status label's tooltip, which stayed invisible until hovered,
  so a misconfigured endpoint looked like silence
- "Test connection" in Provider Profiles: probe the endpoint you are
  editing and see how many models it returns, or the transport error if it
  cannot be reached. The probe runs on a worker thread, so the dialog stays
  usable while the request is in flight, and every profile keeps its own
  last result
- inline notes in Provider Profiles for a Base URL with no scheme or no
  host, for one that already includes the request path (the client appends
  `/v1` and asks for `/models` itself, so it would be requested twice), and
  for a remote endpoint left without an API key. A mistyped endpoint used
  to fail silently: its models were simply missing from the dropdown
- a warning before saving an MCP host or port that would restart the
  embedded server and drop any connected clients
- inline notes for two more inputs that used to fail silently: an Ollama
  host written without a scheme, and an MCP listen host written as a URL
  or carrying a port

### Changed

- Assistant Settings is now four tabs grouped by task (Chat, Completions,
  Appearance and Advanced) instead of seven. Completion options were
  spread across three of them, and Temperature and Max tokens appeared in
  two tabs with no stated relationship
- the per-tab settings are called "Tab settings" in the menu, the dialog
  title and the tooltip, which now also explains what the "*" on the
  button means. One concept had three names, and the Settings button
  opened a different dialog on click than its arrow did, so the per-tab
  settings hid behind the arrow (GitHub issue #4)
- a long conversation no longer gets slower with every message. Each new
  message used to re-lay out the whole transcript, so cost grew with
  length; measured over 150 exchanges, the last messages took 101 ms each
  against 6.5 ms for the first, 7.9 s in total. Finished messages are now
  appended to the document instead, which takes 0.18 s for the same 150
  and stays flat as the chat grows
- syntax highlighting is reused instead of recomputed. While a response
  streams, every code block already closed inside it was re-highlighted
  about 30 times a second even though it could no longer change; a
  response with a dozen closed blocks now costs 2.5 ms per render instead
  of 6.2 ms, and its markdown re-render dropped from 3.5 ms to 0.17 ms
- project and git tools (`project.search`, `git.diff`, and the rest) now run
  on a worker thread instead of inside the chat turn's Qt slot, so a
  project-wide search or a slow git command no longer freezes the editor,
  the consoles and the transcript while it runs. Measured over a 1900-file
  project: the worst event-loop stall for one search dropped from about
  700 ms to about 70 ms
- while the model is waiting on a project or git tool, the chat status line
  names the tool that is running instead of showing an unexplained spinner,
  and the turn stays open so `Send` re-enables only once the answer arrives
- the embedded MCP server now starts on the first idle pass of the event
  loop rather than during plugin initialization, keeping its startup
  handshake off Spyder's startup path
- MCP project and git requests no longer move their file and git work onto
  the GUI thread; only the project-root resolution runs there
- inspecting several variables at once now opens one kernel client for the
  batch and stops fetching live values after a 3 second budget, instead of
  one blocking kernel round trip per variable

### Fixed

- the Copy and Apply actions under a code block are no longer mouse-only.
  The chat menu gained "Copy last code block" and "Apply last code
  block...", and placing the text cursor on either link in the transcript
  and pressing Return, Enter or Space now activates it
- the chat pane now has an explicit tab order (model, console, transcript,
  input, then the action row) instead of following the order its widgets
  happened to be built in
- focus returns to the input after sending, rather than staying on the Send
  button
- the colour swatches in Appearance were labelled "User message background"
  and "User message text", which wrapped in their narrow columns so both
  showed "User message" and looked like the same swatch. All eight labels
  now fit on one line
- the kernel indicator says "Kernel: none" when no console has been opened,
  instead of "unavailable", which read like a fault before anything had
  started
- a long file path in the context label is shortened with an ellipsis in the
  middle and keeps the full path in its tooltip; in a narrow dock it was
  simply cut off
- dimmed reasoning text, error text and one message label were hard to read
  in several colour presets. Measuring every preset in both interface modes
  found six colour pairs below a 3:1 contrast ratio, the worst being nord's
  reasoning text at 1.69:1 against its own background. Those six were
  re-derived by keeping each palette's hue and saturation and adjusting only
  lightness, so the themes still look like themselves; ordinary message text
  was never affected and stays above 8:1
- the chat history browser drew the storage path in a fixed grey that
  ignored the interface theme and was close to unreadable on a dark one
- a provider failure now says what to do about it instead of showing an
  exception. An unreachable local Ollama says to start it with
  `ollama serve`, a missing model gives the `ollama pull` command for that
  model, and a rejected API key, a rate limit and a server error each get
  their own message. Two paths used to put raw exception text in front of
  the user: the chat transcript's last-resort fallback, and the provider
  diagnostics behind the status label, which rendered the exception
  directly. Failures are also classified by exception type and HTTP status
  rather than by searching the message for words like "refused", which
  only ever matched one library's wording
- the status label no longer reads "1 provider issue" for one issue and
  "2 provider issue" for two
- restored the MCP editor tools: `preview_file_edit` and `apply_file_edit`
  raised `NameError` because the shared plugin lookup was not imported when
  it was centralised
- the message shown when project file access is switched off pointed at the
  old Behavior tab instead of Advanced
- the embedded MCP server no longer logs a reconfiguration line for
  configuration changes that leave it unchanged
- ghost text managers are released when a file closes. Every closed editor
  used to keep its manager, two timers, three shortcuts and three event
  filters alive for the rest of the session, and because CPython reuses
  id() values a later editor could inherit a stale manager and show
  another file's ghost text
- the Advanced settings page no longer draws the OpenCode buttons over the
  JSON box or cuts a line from the MCP status note. It needed 950px and
  was given 883; every page now sits in a scroll area, and the dialog
  opens at 820x900 and can shrink on small screens
- a setting stored outside its range is corrected consistently now that
  the bounds have one owner. A stored chat font size of 999 shows as 24, a
  bubble spacing of -5 shows as 0, and an idle delay of 1 shows as 100;
  they used to be written twice and could disagree
- three unguarded plugin lookups would raise on Spyder builds whose
  `get_plugin` does not take an `error` keyword

## 0.7.2 - 2026-09-12

### Fixed

- removed the large empty gap between a paragraph and a following code
  block in chat answers; lists, tables, blockquotes, headings and rules
  no longer keep blank lines above them either

### Documentation

- rewrote the README around a short quick start and feature tour, with
  reference material in collapsible sections and descriptions that match the
  current menus, settings tabs and MCP tool list
- regenerated the README screenshots from a live Spyder with a new
  `run_readme_screenshots` harness; images now use absolute URLs so they also
  render on PyPI
- updated the package description to mention OpenAI-compatible providers and
  the MCP server

## 0.7.1 - 2026-09-10

### Fixed

- restored editor context-menu integration on Spyder 6.1.4 and newer while
  retaining compatibility with the per-editor menus in older Spyder releases
- prevented FastMCP from installing a global Rich stderr logger that Spyder
  reported as an internal error, and coalesced redundant MCP startup events
- preserved provider profiles and credentials when saving the parent
  Assistant Settings dialog

## 0.7.0 - 2026-09-06

### Inline completions

- ghost text now owns the editor by default: while the AI model is available,
  Spyder's automatic completion popup (pylsp) stays hidden and ghost text is
  the only automatic suggestion; `Ctrl+Space` still opens the native popup.
  New `native_popup_policy` setting (Settings > Behavior) with
  `ai_first` (default), `ai_replaces`, and `native_first`
- when the configured completion model is not installed, completions fall
  back to the chat model at runtime; the status bar shows the model in use
  and explains the fallback in its tooltip
- the completion model is warmed up at startup and kept resident
  (`keep_alive`), so the first suggestion no longer waits for a cold load
- fixed ghost text being paused by the native popup taking focus, stale
  anchors while a ghost was visible, lost targets on dismissal, and duplicate
  manual requests from the two shortcut filters
- manual AI requests sync the document without the visible ghost text
- live validation against a real language server (`run_completion_lsp_validation`)

### Chat

- streaming renders into a document frame with coalesced 33 ms updates
  (no per-token re-layout, no horizontal scrollbar from `adjustSize()`)
- code blocks pick the Pygments style from the code-card luminance so light
  themes stay readable
- **Apply** gained a "replace the existing function/class of the same name"
  mode (AST-based, decorators included) next to insert / replace selection,
  with a unified-diff preview that handles newline-only changes
- the chat mode (Coding, Debugging, Review, Data Analysis, Explanation,
  Documentation) moved from the toolbar into the per-tab Chat Settings dialog;
  the `Settings` button reads `Settings*` while a tab deviates from defaults
- responsive action row with icons; new tabs are titled "Chat 1", "Chat 2", ...
  and session timestamps are refreshed on activity
- model selection shares one labelling/priority helper between the toolbar
  and the settings dialog; the provider prefix only appears with several
  providers

### Project files and git

- the model can, on request, list/read/search project files and read
  `git status` / `git diff` / `git log`, bounded to the project root with
  size caps (`project_tools_enabled` in Settings > Behavior)
- a **Review Changes** action in the Debug menu asks the model to review the
  uncommitted changes
- the same tools are exposed over the embedded MCP server (17 tools total);
  `uvicorn` runs with `log_config=None` so Spyder's logging setup cannot
  break it

### Reliability

- provider clients are closed on settings changes and shutdown
- plugin logging is initialised in real sessions
  (`~/.config/spyder-py3/spyder-ai-assistant.log`)
- unit tests and live harnesses never touch the real Spyder config
  (`SPYDER_PYTEST`, audit hook); harnesses run on a private Xvfb display

## 0.6.0 - 2026-03-13

- appearance, behavior, and theme settings in the assistant dialog
- refreshed README screenshots for chat, completions, and settings
- prefix-replay stripping for ghost text completions

## 0.5.0 - 2026-03-13

- multi-provider completion backend (Ollama and OpenAI-compatible profiles)
  with a settings dialog and ghost text enhancements
- robust manual completion shortcut handling
- smart scroll and markdown rendering improvements in the chat display
- CI build fixed to use `python -m build`

## 0.4.0 - 2026-03-12

### Integration fixes

- fixed Spyder startup against legacy `chat_temperature = 0.5` configs by
  resetting invalid stored values to the current integer-backed preference
  format automatically
- guarded provider/model sync callbacks during early startup so config-change
  notifications do not touch the dock widget before it exists

### Phase 13: UX polish and discovery

- reduced chat-pane control clutter again by replacing separate `History` and
  `More` buttons with one `Sessions` button that opens history on click and
  exposes lower-frequency actions from its menu
- expanded the built-in prompt-mode library with `Review` and `Data Analysis`
  presets aimed at real code-review and scientific/debugging workflows
- upgraded the history browser with prompt-mode metadata, free-text search,
  open-vs-saved filtering, and multiple sort modes
- added tracked unit coverage for prompt/library and history filtering plus a
  live Spyder validation harness for searchable session discovery

### Phase 12: Provider ergonomics

- replaced the single OpenAI-compatible endpoint flow with named compatible
  provider profiles managed from the chat pane
- added a profile manager dialog for create, duplicate, edit, enable/disable,
  and delete actions without leaving the main chat workflow
- expanded provider diagnostics so the status label and model tooltip report
  per-profile readiness, endpoint identity, and failure details
- preserved working providers when one profile fails, including clean fallback
  when a selected profile is removed
- migrated legacy single-endpoint settings into the profile store the first
  time the new dialog is used
- added tracked unit coverage and a live Spyder validation harness for
  multi-profile selection, auth-header routing, diagnostics, and stale-profile
  fallback behavior

### Phase 10: Deeper terminal and kernel integration

- added explicit multi-console runtime targeting in the chat toolbar with
  `Follow Active Console` and pinned-shell inspection
- added `runtime.list_shells` to the runtime bridge so chat can reason about
  available consoles and their active/target/error state
- expanded runtime inspection summaries for list-backed arrays, images,
  pandas objects, and bounded nested containers
- normalized traceback summaries for both file-backed Python frames and
  IPython `Cell In[...]` frames
- tightened runtime request metadata so results carry shell, active-shell, and
  target-shell identity consistently
- added a tracked live Spyder validation harness for multi-console runtime
  targeting, richer variable inspection, and frame-aware traceback inspection

### Phase 11: Edit UX and diff/apply refinement

- reduced the visible chat control clutter by collapsing runtime quick actions
  into a compact `Debug` menu and moving lower-frequency session actions into
  `More`
- replaced code-block `Insert at cursor` / `Replace selection` links with a
  safer `Apply...` preview dialog
- added unified-diff previews, explicit accept/cancel, and mode selection for
  insert-vs-replace before mutating the editor
- grouped previewed editor mutations into single-step undo operations
- added a tracked live Spyder validation harness for cancel/apply/undo coverage
  on both insert and replace workflows

### Phase 7: Completion polish

- added partial ghost acceptance for the next word-like segment and next line
- added a small local LRU completion cache for repeated prompt states
- trimmed completion suffix overlap before display to avoid duplicated trailing text
- filtered obviously repetitive low-value completions before they reach the editor
- added local completion lifecycle counters and exposed them through the status tooltip
- blocked Spyder's native completion popup when an AI ghost suggestion is already active
- expanded tracked live completion validation to cover cache, overlap, repetition,
  partial accept, popup suppression, and recovery behavior

### Phase 8: Multi-provider chat foundation

- added a provider-aware chat backend registry for the dockable chat pane
- added an OpenAI-compatible chat transport with `/v1/models` discovery and
  streaming `/v1/chat/completions` support
- updated the chat model selector to show provider-aware entries and tooltips
- added provider settings to the preferences page, including an optional
  compatible API key and default chat-provider selection
- expanded live Spyder validation with a fake OpenAI-compatible endpoint and
  same-session switching back to a real Ollama model

### Phase 9: Advanced completion intelligence

- added relevant neighbor-file snippet selection for completion prompts
- added alternative-candidate generation and local cycling for repeated
  requests on the same visible completion target
- added deterministic candidate scoring to keep remembered alternatives ordered
- expanded completion validation and unit coverage for neighbor context,
  alternative generation, and local cycling behavior
