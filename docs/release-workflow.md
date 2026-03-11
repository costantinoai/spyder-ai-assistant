# Release Workflow

This project publishes from tags.

Pushing a version tag such as `vX.Y.Z` triggers [.github/workflows/publish.yml](../.github/workflows/publish.yml), which:

1. builds the sdist and wheel from a clean tree
2. publishes them to PyPI through trusted publishing
3. creates a GitHub Release for the same tag

## Current action baseline

The release workflow is pinned to the current Node 24-ready major versions of the official GitHub actions:

- `actions/checkout@v6`
- `actions/setup-python@v6`
- `actions/upload-artifact@v6`
- `actions/download-artifact@v8`
- `pypa/gh-action-pypi-publish@release/v1`

This avoids the GitHub-hosted runner warning about Node 20 action runtimes on release jobs.

## Self-hosted runner note

The repository releases on `ubuntu-latest`, so nothing extra is required on GitHub-hosted runners.

If you ever mirror this workflow to self-hosted runners, keep the runner version current. A practical floor for this workflow is `2.329.0+`, which stays ahead of the Node 24 transition and the current `checkout@v6` behavior.

## Typical release commands

```bash
git checkout main
git pull --ff-only
python -m tools.release.build_dist
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

The tracked build helper removes stale `build/`, `dist/`, and generated
`.egg-info` directories before rebuilding. That avoids local contamination from
older package names and keeps CI and local release checks aligned.

## Post-release checks

After pushing the tag:

1. watch the workflow:

```bash
gh run watch --exit-status
```

2. confirm the GitHub Release exists:

```bash
gh release view vX.Y.Z
```

3. confirm PyPI serves the new package:

```bash
python -m pip index versions spyder-ai-assistant
```

4. reinstall the published package into the real `spyder-ai` environment and
run the tracked live validation harnesses from
[docs/validation-workflow.md](validation-workflow.md).

If all four pass, the release path is healthy.
