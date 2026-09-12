# Validation Workflow

This document describes the tracked validation harnesses used to verify the
plugin in a real Spyder session.

These validations are not a substitute for focused unit tests. They are the
integration layer that checks the shipped plugin inside the real `spyder-ai`
environment, with Spyder actually launched and logs reviewed.

## Environment

All live validation commands assume:

- the `spyder-ai` conda environment exists
- Spyder is installed in that environment
- Ollama is available at the configured host
- a display is available, and it is a **private** one, not the display you
  work on. Every automated run goes to a virtual display (start one with
  `Xvfb :99 -screen 0 3840x2160x24`); the screenshot harness needs that
  size. Driving a harness on your own session steals focus and keystrokes
  from whatever you are doing.

Activate the environment first:

```bash
source ~/miniforge3/etc/profile.d/conda.sh
conda activate spyder-ai
```

## Validation package

Tracked validation harnesses live under:

```text
tools/spyder_validation/
```

The main entry points are:

- `python -m tools.spyder_validation.run_chat_provider_validation`
- `python -m tools.spyder_validation.run_completion_validation`
- `python -m tools.spyder_validation.run_chat_workflow_validation`
- `python -m tools.spyder_validation.run_chat_persistence_setup`
- `python -m tools.spyder_validation.run_chat_persistence_verify`
- `python -m tools.spyder_validation.run_chat_prompt_preset_validation`
- `python -m tools.spyder_validation.run_chat_prompt_preset_restore_validation`
- `python -m tools.spyder_validation.run_chat_inference_controls_validation`
- `python -m tools.spyder_validation.run_chat_inference_controls_restore_validation`
- `python -m tools.spyder_validation.run_chat_exchange_deletion_validation`
- `python -m tools.spyder_validation.run_chat_exchange_deletion_restore_validation`
- `python -m tools.spyder_validation.run_chat_history_browser_validation`
- `python -m tools.spyder_validation.run_chat_history_browser_restore_validation`
- `python -m tools.spyder_validation.run_chat_use_console_smoke`
- `python -m tools.spyder_validation.run_chat_tool_responsiveness_validation`
- `python -m tools.spyder_validation.run_phase10_runtime_validation`
- `python -m tools.spyder_validation.run_phase11_apply_preview_validation`
- `python -m tools.spyder_validation.run_phase12_provider_profiles_validation`
- `python -m tools.spyder_validation.run_phase13_history_discovery_validation`
- `python -m tools.spyder_validation.run_readme_screenshots`

## Typical full validation pass

```bash
PYTHONPATH=src pytest
python -m tools.release.build_dist
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_completion_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_provider_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_workflow_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_persistence_setup
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_persistence_verify
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_prompt_preset_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_prompt_preset_restore_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_inference_controls_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_inference_controls_restore_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_exchange_deletion_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_exchange_deletion_restore_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_history_browser_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_history_browser_restore_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_use_console_smoke
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_chat_tool_responsiveness_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_phase10_runtime_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_phase11_apply_preview_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_phase12_provider_profiles_validation
DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_phase13_history_discovery_validation
```

`python -m tools.release.build_dist` is the preferred packaging check because
it clears stale local build artifacts before rebuilding the sdist and wheel.

## What each harness checks

### Completion validation

- provider startup
- fake-model deterministic completion checks
- partial accept by next word and next line
- suffix-overlap trimming
- repetition filtering
- cache warm and cache hit behavior
- native popup ownership: `ai_first` hides automatic popups, `ai_replaces` closes them when the ghost arrives, `native_first` lets them win (`run_completion_lsp_validation`, real pylsp)
- stale completion discard
- single-line and multiline ghost text
- Tab accept and Escape dismiss
- typed-through ghost continuation
- neighbor-file snippet injection from other open documents
- alternative-candidate generation and local cycling on repeated requests
- local completion metrics snapshot
- real-model completion smoke
- offline host recovery

### Chat provider validation

- provider-aware model discovery in the shared chat selector
- OpenAI-compatible `/v1/models` discovery against a local fake endpoint
- OpenAI-compatible streaming `/v1/chat/completions` validation with API-key handling
- switch back to a real Ollama model in the same Spyder session
- confirm both providers can answer from the same chat pane

### Chat workflow validation

- plugin startup
- runtime status label
- debug quick actions
- runtime bridge behavior
- code apply actions
- regenerate
- export metadata

### Persistence setup and verify

- project-scoped chat persistence write
- project reopen and state restore
- active tab restore
- session title and message restore

### Prompt preset validation

- per-tab preset selection through the shared toolbar combo
- deterministic prompt block checks for the selected mode
- tab switching keeps the selector aligned with the active session
- persisted `prompt_preset_id` values are written to the project state file

### Prompt preset restore validation

- reopen Spyder against the same project after preset selection
- confirm restored tabs keep their saved preset ids
- confirm the shared toolbar combo follows the restored active tab

### Inference-controls validation

- open the real per-tab `Settings` dialog from the chat pane
- apply temperature and max-token overrides to one tab
- exercise reset-to-global-defaults on another tab
- send real prompts and confirm different resolved request options per tab
- verify saved session state contains the expected override fields
- confirm the live validation log records the resolved options and saved overrides

### Inference-controls restore validation

- reopen Spyder against the same project after saving per-tab overrides
- confirm restored tabs keep their saved override fields
- confirm the `Settings` button and tooltip follow the restored active tab
- confirm resolved request options after restart still match the saved state

### Exchange-deletion validation

- open the real delete-exchange dialog from the active chat tab
- delete a middle exchange from a multi-turn conversation
- confirm the visible transcript loses only that selected exchange
- confirm regenerate still works on the remaining last turn
- confirm the saved session file no longer contains the deleted exchange

### Exchange-deletion restore validation

- reopen Spyder against the same project after deleting an exchange
- confirm the deleted turn stays gone after restart
- confirm the delete browser now lists only the remaining exchanges
- confirm restored transcript order still matches the saved session file

### History browser validation

- real `History` button and modal dialog path
- visible browser rows and open/saved status
- reopen from saved history
- duplicate into a fresh session id
- delete from history and close the matching open tab
- persist the resulting history state to the project storage file

### History browser restore validation

- reopen Spyder against the same project after browser actions
- confirm restored tabs, titles, and active index
- confirm deleted history sessions stay deleted across restart

### Use-console smoke

- focused verification that the console quick action inspects the visible
  console tail and answers using the actual marker printed in the current run

### Phase 10 runtime validation

- create a second live IPython console in the same Spyder session
- seed distinct runtime state into both consoles
- pin the chat runtime selector to the non-active console
- verify shell listing and target flags
- verify DataFrame inspection
- verify array shape, dtype, and numeric range
- verify traceback summary with at least one parsed frame
- send one real chat request against the pinned console and confirm the answer
  uses that console's runtime data

### Phase 11 apply-preview validation

- validate the compact control row in the live chat pane
- open the real apply-preview dialog from a code block signal path
- confirm cancel leaves the editor unchanged for insert and replace flows
- confirm apply mutates the editor correctly for insert and replace flows
- confirm one undo restores the prior editor text after each apply
- capture the preview diff text in the JSON artifact and terminal log

### Phase 12 provider-profiles validation

- open the real `Provider Profiles...` dialog from the chat pane
- save multiple named compatible profiles through the dialog
- verify diagnostics for ready and failing profiles in the live status tooltip
- verify provider-specific API keys are sent to the matching endpoint
- switch between multiple compatible profiles in the shared model selector
- remove one compatible profile and confirm stale selection falls back cleanly
- confirm a failing profile does not prevent working profiles from answering

### Phase 13 history-discovery validation

- open the real `Sessions` button path from the chat pane
- verify the compact session menu labels in the live toolbar
- search the history browser by title and by prompt mode
- filter rows to `Saved only`
- sort saved rows alphabetically
- reopen one saved session from a filtered view
- confirm the active session switches to the reopened row

### Tool responsiveness validation

- run a 10 ms timer on the GUI thread throughout, so the longest stretch
  the event loop went unserviced *is* the freeze a user would have felt
- run the same `project.search` twice over a ~1900-file fixture: once
  through the chat path (worker thread) and once inline on the GUI thread,
  both on a warm page cache, and record both stalls
- include a guard check that the inline path really did block, so a fixture
  that is too fast cannot pass the comparison for the wrong reason
- drive one real turn through a `git.status` tool call with the widget's
  signals blocked, so no model is needed, and confirm the turn stays
  `generating`, Send stays disabled, and the status line names the tool
- capture the pending state as a screenshot for review
- confirm from the plugin log that the embedded MCP server starts *after*
  plugin initialization returns, keeping its startup handshake off Spyder's
  boot path
- needs no Ollama model, so it is deterministic

### README screenshots

- regenerate the README images in `docs/screenshots/` (chat panel, ghost
  completions, apply preview, editor context menu, Debug menu, history
  browser, Models and MCP settings) from a live Spyder in the dark theme
- stage all content itself (fake model list, staged transcripts, a fake
  completion backend, seeded history sessions), so no Ollama model is needed
- run Qt at 2x scale: panes and dialogs are rendered as HiDPI widget images,
  popup menus are captured with a screen-region grab at the same density
- fail when any shot is missing or the MCP tab does not show a running
  server; the JSON artifact records each shot's size and staged evidence
- run it on a private display only, e.g.
  `DISPLAY=:99 PYTHONPATH=src python -m tools.spyder_validation.run_readme_screenshots`,
  then review every image before committing

## Artifact locations

The harnesses write JSON results and log files under:

```text
/tmp/spyder-ai-assistant-validation/
```

Each run writes:

- a JSON summary
- a terminal log captured from Spyder
- a process exit code that is `0` on success and non-zero on validation failure

## Review expectations

A validation run is only considered acceptable when both are true:

1. the JSON result reports no errors
2. the log confirms the intended path actually ran

For example:

- runtime tests should show `runtime.get_latest_error`,
  `runtime.list_variables`, or `runtime.get_console_tail`
- persistence tests should show save and restore log lines
- completion tests should show provider startup and clean shutdown
- completion tests should show cache-hit, overlap, repetition, popup-block,
  neighbor-context, and candidate-cycling behavior in the JSON artifact
  and terminal log
- provider validation should show compatible-provider configuration, provider
  model discovery, one compatible response, and one Ollama response in both
  the JSON artifact and terminal log
- history-browser tests should show dialog creation plus reopen, duplicate,
  and delete log lines
- Phase 10 runtime validation should show array and traceback summary lines plus
  the shell-target map and the final pinned-console chat answer
- Phase 11 apply-preview validation should show insert/replace preview diffs and
  the undo checkpoints in both the JSON artifact and terminal log
- Phase 12 provider-profile validation should show provider diagnostics, two
  distinct compatible answers, profile-specific auth headers, and a clean stale
  profile fallback in both the JSON artifact and terminal log
- Phase 13 history-discovery validation should show the filtered row sets, sort
  order, session-menu actions, and the final reopened session in both the JSON
  artifact and terminal log
- prompt-preset tests should show preset selection log lines and restored
  preset ids in the JSON artifact
- inference-control tests should show per-tab resolved options, reset behavior,
  and restored override state in both the log and JSON artifacts
- exchange-deletion tests should show the deleted-turn gap in both the restored
  transcript and the delete-browser row list
- restore tests should show the expected save and restore counts
- tool-responsiveness validation should show the async stall far below the
  inline stall, a non-`MainThread` worker name, `runtime_pending` for the
  turn, and the MCP start marker appearing after the plugin-init marker in
  the plugin log inside the isolated conf dir

## Release usage

For releases, run the harnesses twice:

1. against the branch or merged checkout before tagging
2. again after installing the published package from PyPI into `spyder-ai`

That second pass is important. It catches packaging or entry-point issues that
would not appear in an editable install.
