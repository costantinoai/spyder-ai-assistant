# Changelog

## Unreleased

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
