# Release Workflow

This project publishes from tags.

Pushing a version tag such as `v0.2.0` triggers [.github/workflows/publish.yml](../.github/workflows/publish.yml), which:

1. builds the sdist and wheel
2. publishes them to PyPI through trusted publishing
3. creates a GitHub Release for the same tag

## Current action baseline

The release workflow is pinned to the current Node 24-ready major versions of the official GitHub actions:

- `actions/checkout@v6`
- `actions/setup-python@v6`
- `actions/upload-artifact@v6`
- `actions/download-artifact@v5`
- `pypa/gh-action-pypi-publish@release/v1`

This avoids the GitHub-hosted runner warning about Node 20 action runtimes on release jobs.

## Self-hosted runner note

The repository releases on `ubuntu-latest`, so nothing extra is required on GitHub-hosted runners.

If you ever mirror this workflow to self-hosted runners, keep the runner version current. A practical floor for this workflow is `2.329.0+`, which stays ahead of the Node 24 transition and the current `checkout@v6` behavior.

## Typical release commands

```bash
git checkout main
git pull --ff-only
git tag -a v0.2.0 -m "v0.2.0"
git push origin main
git push origin v0.2.0
```

## Post-release checks

After pushing the tag:

1. watch the workflow:

```bash
gh run watch --exit-status
```

2. confirm the GitHub Release exists:

```bash
gh release view v0.2.0
```

3. confirm PyPI serves the new package:

```bash
python -m pip index versions spyder-ai-assistant
```

If all three pass, the release path is healthy.
