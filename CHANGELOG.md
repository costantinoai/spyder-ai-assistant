# Changelog

## Unreleased

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
