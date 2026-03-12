# Changelog

## Unreleased

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
