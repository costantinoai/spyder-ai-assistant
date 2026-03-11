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
