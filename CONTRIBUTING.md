# Contributing

This project is in early alpha. It works on my machine, but it hasn't been widely tested yet. If you try it out, I'd genuinely appreciate hearing about your experience — what works, what breaks, what's missing.

Open an issue at [github.com/costantinoai/spyder-ai-assistant/issues](https://github.com/costantinoai/spyder-ai-assistant/issues) for bug reports, feature requests, or general feedback. Pull requests are welcome too.

## Development setup

```bash
git clone https://github.com/costantinoai/spyder-ai-assistant.git
cd spyder-ai-assistant
pip install -e ".[dev]"
pytest          # run tests
spyder          # launch with the plugin
```

Install into the same Python environment where Spyder lives (e.g., your conda env).

Requires `spyder >= 6.0.0`, `ollama >= 0.4.0`, and Python 3.11+. Pygments (for syntax highlighting) ships with Spyder.

## Releasing a new version

Pushing a version tag triggers the full release pipeline automatically — build, PyPI publish, and GitHub Release creation. No manual steps needed beyond the tag.

```bash
git tag -a v0.2.0 -m "v0.2.0: Short description of the release"
git push origin v0.2.0
```

The version number is derived from the git tag by [setuptools-scm](https://github.com/pypa/setuptools-scm) — there is no hardcoded version string to update. Between tags, development installs report a version like `0.2.1.dev3` (3 commits after v0.2.0).

The pipeline runs in three stages:

1. **Build** — builds the sdist and wheel on Ubuntu with Python 3.11
2. **Publish** — uploads to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/) (OIDC, no API tokens)
3. **Release** — creates a GitHub Release with auto-generated notes
