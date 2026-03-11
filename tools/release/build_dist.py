"""Build a clean sdist and wheel for release validation.

This helper removes stale local build artifacts before invoking ``python -m
build``. That keeps local release checks and CI runs aligned, and it avoids
carrying old package trees forward after repository renames or packaging
changes.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOTS = [
    REPO_ROOT / "build",
    REPO_ROOT / "dist",
    REPO_ROOT / "src" / "spyder_ai_assistant.egg-info",
]


def _remove_path(path: Path) -> None:
    """Delete one generated build path if it exists."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _run_build(*args: str) -> None:
    """Run one build command from the repository root."""
    subprocess.run(
        [sys.executable, "-m", "build", *args],
        cwd=REPO_ROOT,
        check=True,
    )


def main() -> int:
    """Build fresh source and wheel distributions."""
    for path in BUILD_ROOTS:
        _remove_path(path)

    _run_build("--sdist")
    _run_build("--wheel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
