"""Rounding for the calculated values, with halves going away from zero."""

import math


def round_half_away(value: float, decimals: int = 0) -> float:
    """Round to `decimals` places; a half goes away from zero (-2.5 -> -3)."""
    if not math.isfinite(value):
        return value
    scale = 10**decimals
    # Rounded first: halves such as 0.015 are not exact in binary
    units = math.floor(round(abs(value) * scale, 6) + 0.5)
    # The sign goes on a whole number, so a small negative value gives 0, not -0
    return (-units if value < 0 else units) / scale
