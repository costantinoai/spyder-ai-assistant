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
- a display is available, typically `DISPLAY=:1`

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

- `python -m tools.spyder_validation.run_completion_validation`
- `python -m tools.spyder_validation.run_chat_workflow_validation`
- `python -m tools.spyder_validation.run_chat_persistence_setup`
- `python -m tools.spyder_validation.run_chat_persistence_verify`
- `python -m tools.spyder_validation.run_chat_prompt_preset_validation`
- `python -m tools.spyder_validation.run_chat_prompt_preset_restore_validation`
- `python -m tools.spyder_validation.run_chat_history_browser_validation`
- `python -m tools.spyder_validation.run_chat_history_browser_restore_validation`
- `python -m tools.spyder_validation.run_chat_use_console_smoke`

## Typical full validation pass

```bash
PYTHONPATH=src pytest
python -m tools.release.build_dist
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_completion_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_workflow_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_persistence_setup
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_persistence_verify
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_prompt_preset_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_prompt_preset_restore_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_history_browser_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_history_browser_restore_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_use_console_smoke
```

`python -m tools.release.build_dist` is the preferred packaging check because
it clears stale local build artifacts before rebuilding the sdist and wheel.

## What each harness checks

### Completion validation

- provider startup
- fake-model deterministic completion checks
- stale completion discard
- single-line and multiline ghost text
- Tab accept and Escape dismiss
- typed-through ghost continuation
- real-model completion smoke
- offline host recovery

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
- history-browser tests should show dialog creation plus reopen, duplicate,
  and delete log lines
- prompt-preset tests should show preset selection log lines and restored
  preset ids in the JSON artifact
- restore tests should show the expected save and restore counts

## Release usage

For releases, run the harnesses twice:

1. against the branch or merged checkout before tagging
2. again after installing the published package from PyPI into `spyder-ai`

That second pass is important. It catches packaging or entry-point issues that
would not appear in an editable install.
