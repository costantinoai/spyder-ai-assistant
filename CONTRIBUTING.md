# Contributing

This project is in early alpha. If you try it out, I'd genuinely appreciate hearing about your experience — what works, what breaks, what's missing.

Open an issue at [github.com/costantinoai/spyder-ai-assistant/issues](https://github.com/costantinoai/spyder-ai-assistant/issues) for bug reports, feature requests, or general feedback. Pull requests are welcome too.

## Development setup

```bash
git clone https://github.com/costantinoai/spyder-ai-assistant.git
cd spyder-ai-assistant
pip install -e ".[dev]"
pytest          # run focused tests
spyder          # launch with the plugin
```

Install into the same Python environment where Spyder lives (e.g., your conda env).

Requires `spyder >= 6.0.0`, `ollama >= 0.4.0`, and Python 3.11+. Pygments (for syntax highlighting) ships with Spyder.

## Architecture

The plugin registers two independent components via separate entry points:

```
spyder.plugins     →  AIChatPlugin (SpyderDockablePlugin)
                      Chat panel, editor integration, context menu actions

spyder.completions →  AIChatCompletionProvider (SpyderCompletionProvider)
                      Inline code completions, status bar widget
```

The chat and completion paths share some context and prompt utilities, but the transports are intentionally separate:

- Chat uses a provider registry with Ollama plus optional OpenAI-compatible backends
- Completions stay Ollama-only and optimize for low-latency inline behavior

### Source layout

```
src/spyder_ai_assistant/
├── plugin.py                 # Main plugin: pane, menus, actions
├── completion_provider.py    # Completion provider: FIM completions
├── backend/
│   ├── client.py             # OllamaClient: Ollama API wrapper
│   ├── chat_providers.py     # Provider registry + OpenAI-compatible chat backend
│   └── worker.py             # ChatWorker: QThread bridge for provider-aware chat
├── utils/
│   ├── completion_context.py # Neighbor-file snippet selection + candidate scoring
│   ├── context.py            # Editor/project context + prompt assembly
│   ├── chat_inference.py     # Per-tab chat option normalization/resolution
│   ├── chat_exchanges.py     # Exchange browsing and deletion helpers
│   ├── chat_persistence.py   # Project/global chat session storage
│   ├── prompt_library.py     # Built-in per-tab chat modes
│   ├── runtime_bridge.py     # Read-only runtime inspection protocol
│   └── runtime_context.py    # Live shell snapshot service
├── widgets/
│   ├── chat_widget.py        # Chat pane: tabs, toolbar, input
│   ├── chat_display.py       # Message rendering: Markdown, highlighting
│   ├── chat_input.py         # Auto-resizing input text area
│   ├── chat_settings_dialog.py  # Per-tab chat settings UI
│   ├── config_page.py        # Preferences page
│   ├── exchange_delete_dialog.py  # Per-session exchange deletion UI
│   ├── ghost_text.py         # Ghost text overlay for completions
│   ├── session_history_dialog.py  # Saved-session browser UI
│   └── status.py             # Status bar widget
```

## Validation workflow

Automated unit-level checks should be run before any live Spyder validation:

```bash
PYTHONPATH=src pytest
python -m tools.release.build_dist
```

Live validation must be run in the real `spyder-ai` environment with Spyder
actually launched and logs reviewed. The tracked validation harnesses live in
`tools/spyder_validation/`.

Typical validation commands:

```bash
source ~/miniforge3/etc/profile.d/conda.sh
conda activate spyder-ai
PYTHONPATH=src python -m pytest tests
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_completion_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_workflow_validation
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_persistence_setup
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_persistence_verify
DISPLAY=:1 PYTHONPATH=src python -m tools.spyder_validation.run_chat_use_console_smoke
```

See [docs/validation-workflow.md](docs/validation-workflow.md) for the
validation harness layout, artifact locations, and how to interpret the runs.

## Releasing a new version

Pushing a version tag triggers the full release pipeline automatically — build, PyPI publish, and GitHub Release creation. No manual steps needed beyond the tag.

```bash
git tag -a vX.Y.Z -m "vX.Y.Z: Short description of the release"
git push origin vX.Y.Z
```

The version number is derived from the git tag by [setuptools-scm](https://github.com/pypa/setuptools-scm) — there is no hardcoded version string to update. Between tags, development installs report a version like `0.3.1.dev3` (3 commits after v0.3.0).

The pipeline runs in three stages:

1. **Build** — builds the sdist and wheel on Ubuntu with Python 3.11
2. **Publish** — uploads to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/) (OIDC, no API tokens)
3. **Release** — creates a GitHub Release with auto-generated notes

The release workflow uses the Node 24-ready major versions of the official GitHub actions. If you mirror it to self-hosted runners, keep them on Actions Runner `2.329.0+`.

Both local release checks and the GitHub build job use
`python -m tools.release.build_dist`. That helper removes stale `build/`,
`dist/`, and `.egg-info` artifacts before rebuilding.

See [docs/release-workflow.md](docs/release-workflow.md) for the exact workflow components and the post-release verification checklist.

## Further documentation

- [docs/runtime-inspection.md](docs/runtime-inspection.md) — runtime bridge design and validation checklist
- [docs/chat-workflows.md](docs/chat-workflows.md) — chat UX documentation
- [docs/validation-workflow.md](docs/validation-workflow.md) — live Spyder validation harnesses
- [docs/release-workflow.md](docs/release-workflow.md) — tag-driven release pipeline and post-release checks
