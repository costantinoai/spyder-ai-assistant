"""Qt-free project path rules and bounded filesystem traversal."""

from dataclasses import dataclass, field
import os
import time

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", ".tox", ".venv", "venv", "env", ".env", ".eggs",
    "dist", "build", ".spyproject", ".idea", ".vscode",
}
MAX_WALK_ENTRIES = 20_000
MAX_WALK_DEPTH = 100
WALK_TIMEOUT_S = 3.0


def resolve_project_path(root, relative):
    """Resolve a path inside root, including roots that are drive anchors."""
    candidate = str(relative or "").strip()
    joined = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
    resolved = os.path.realpath(joined)
    try:
        contained = os.path.normcase(os.path.commonpath((root, resolved))) == os.path.normcase(root)
    except ValueError:
        contained = False
    if not contained:
        raise ValueError(
            f"Path {candidate!r} is outside the project root; only files "
            "under the project can be accessed."
        )
    return resolved


@dataclass
class WalkBudget:
    max_entries: int = MAX_WALK_ENTRIES
    timeout_s: float = WALK_TIMEOUT_S
    visited: int = 0
    reason: str = ""
    deadline: float = field(init=False)

    def __post_init__(self):
        self.deadline = time.monotonic() + self.timeout_s

    def exhausted(self):
        if time.monotonic() >= self.deadline:
            self.reason = "time budget"
        elif self.visited >= self.max_entries:
            self.reason = "traversal budget"
        return bool(self.reason)


def walk_project_files(root, start, budget):
    """Visit bounded directory entries, never following directory symlinks."""
    pending = [(start, 0)]
    while pending and not budget.exhausted():
        directory, depth = pending.pop()
        directories = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if budget.exhausted():
                        return
                    budget.visited += 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                                continue
                            if depth >= MAX_WALK_DEPTH:
                                budget.reason = "depth budget"
                                return
                            directories.append((entry.path, depth + 1))
                        elif entry.is_file():
                            resolved = resolve_project_path(root, entry.path)
                            yield os.path.relpath(entry.path, root), resolved
                    except (OSError, ValueError):
                        continue
        except OSError:
            continue
        pending.extend(sorted(directories, reverse=True))
