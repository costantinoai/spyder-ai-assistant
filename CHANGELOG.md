# Changelog

## Unreleased

### Added

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

### Changed

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

- restored the MCP editor tools: `preview_file_edit` and `apply_file_edit`
  raised `NameError` because the shared plugin lookup was not imported when
  it was centralised
- the message shown when project file access is switched off pointed at the
  old Behavior tab instead of Advanced
- the embedded MCP server no longer logs a reconfiguration line for
  configuration changes that leave it unchanged

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
