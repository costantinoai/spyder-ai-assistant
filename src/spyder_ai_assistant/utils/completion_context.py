"""Helpers for completion-side context selection and candidate scoring."""

from __future__ import annotations

import keyword
import os
import re
from dataclasses import dataclass


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP_TERMS = {
    "self", "true", "false", "none", "return", "yield", "await",
    "import", "from", "class", "def", "pass", "break", "continue",
}


@dataclass(frozen=True)
class CompletionContextSnippet:
    """One relevant neighbor-file snippet for a completion prompt."""

    filename: str
    excerpt: str
    score: int
    matched_terms: tuple[str, ...]


def extract_completion_terms(prefix, current_word="", max_terms=6):
    """Extract the most relevant identifier-like terms near the cursor."""
    ordered = []
    seen = set()

    def add(term):
        normalized = str(term or "").strip()
        if len(normalized) < 2:
            return
        lowered = normalized.lower()
        if lowered in _STOP_TERMS or keyword.iskeyword(lowered):
            return
        if lowered in seen:
            return
        seen.add(lowered)
        ordered.append(normalized)

    add(current_word)
    tail = "\n".join(prefix.splitlines()[-40:])
    for match in reversed(_IDENTIFIER_RE.findall(tail)):
        add(match)
        if len(ordered) >= max_terms:
            break
    return ordered[:max_terms]


def build_related_completion_snippets(filename, prefix, current_word,
                                      document_states, max_snippets=2,
                                      max_excerpt_chars=220):
    """Select small relevant snippets from other tracked documents."""
    terms = extract_completion_terms(prefix, current_word=current_word)
    if not terms:
        return []

    current_filename = os.path.abspath(filename or "")
    rows = []
    for other_filename, state in (document_states or {}).items():
        if not other_filename:
            continue
        if os.path.abspath(other_filename) == current_filename:
            continue

        text = getattr(state, "text", state) or ""
        if not text:
            continue

        snippet = _best_snippet_for_terms(
            other_filename,
            str(text),
            terms,
            max_excerpt_chars=max_excerpt_chars,
        )
        if snippet is not None:
            rows.append(snippet)

    rows.sort(key=lambda item: (-item.score, item.filename))
    return rows[:max_snippets]


def score_completion_candidate(text, current_word="", single_line=False,
                               related_terms=()):
    """Return a small deterministic score for one completion candidate."""
    stripped = str(text or "").strip()
    if not stripped:
        return -10_000

    score = 100
    newline_count = stripped.count("\n")
    if single_line and newline_count:
        score -= 30
    score -= min(len(stripped), 240) // 12

    if current_word:
        if stripped.startswith(current_word):
            score += 12
        elif current_word in stripped:
            score += 6

    matched_terms = 0
    lowered_text = stripped.lower()
    for term in related_terms or ():
        if term.lower() in lowered_text:
            matched_terms += 1
    score += matched_terms * 4

    if stripped[0] in ")]},":
        score -= 4
    if stripped.endswith("()"):
        score += 2

    return score


def _best_snippet_for_terms(filename, text, terms, max_excerpt_chars=220):
    """Return the highest-scoring excerpt from one neighbor document."""
    basename = os.path.basename(filename or "")
    lines = str(text).splitlines()
    best = None

    for index, line in enumerate(lines):
        lowered = line.lower()
        matched_terms = tuple(
            term for term in terms if term.lower() in lowered
        )
        if not matched_terms:
            continue

        score = sum(max(1, lowered.count(term.lower())) for term in matched_terms)
        if any(term.lower() in basename.lower() for term in matched_terms):
            score += 2

        start = max(0, index - 1)
        end = min(len(lines), index + 3)
        excerpt = "\n".join(lines[start:end]).strip()
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[: max_excerpt_chars - 1].rstrip() + "..."

        candidate = CompletionContextSnippet(
            filename=basename or filename,
            excerpt=excerpt,
            score=score,
            matched_terms=matched_terms,
        )
        if best is None or candidate.score > best.score:
            best = candidate

    return best
