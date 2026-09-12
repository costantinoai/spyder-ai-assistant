"""Numeric coercion shared by settings, project tools and runtime inspection.

Three modules each carried their own clamp helper with a slightly different
signature (one floored at 1, one required both bounds, one made them
optional), so a bound corrected in one place stayed wrong in the others.
"""

from __future__ import annotations


def bounded_int(value, default, minimum=None, maximum=None):
    """Return ``value`` as an integer inside the given bounds.

    Args:
        value: Raw value, typically from config or from a tool argument.
        default: Used when ``value`` is not a number.
        minimum: Lower bound, or None to leave the result unbounded below.
        maximum: Upper bound, or None to leave it unbounded above.

    Returns:
        The coerced, clamped integer.
    """
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = int(default)

    if minimum is not None:
        number = max(int(minimum), number)
    if maximum is not None:
        number = min(int(maximum), number)
    return number
