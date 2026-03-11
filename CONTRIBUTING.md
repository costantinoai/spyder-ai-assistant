# Contributing

This project is in early alpha. It works on my machine, but it hasn't been widely tested yet. If you try it out, I'd genuinely appreciate hearing about your experience — what works, what breaks, what's missing.

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

## Validation workflow

Automated unit-level checks should be run before any live Spyder validation:

```bash
PYTHONPATH=src pytest
python -m tools.release.build_dist
```

Live validation must be run in the real `spyder-ai` environment with Spyder
actually launched and logs reviewed. The tracked validation harnesses live in
`tools/spyder_validation/`.

Typical Phase 5 validation commands:

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

The release workflow now uses the Node 24-ready major versions of the official GitHub actions to stay ahead of the GitHub runner deprecation path. If you mirror it to self-hosted runners, keep them on Actions Runner `2.329.0+`.

Both local release checks and the GitHub build job use
`python -m tools.release.build_dist`. That helper removes stale `build/`,
`dist/`, and `.egg-info` artifacts before rebuilding, which keeps renamed
packages or old wheels from leaking into new release artifacts.

See [docs/release-workflow.md](docs/release-workflow.md) for the exact workflow components and the post-release verification checklist, and [docs/validation-workflow.md](docs/validation-workflow.md) for the live Spyder validation steps that should be run before and after a release.
