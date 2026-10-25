"""Pause durations as typed on the command line: `45s`, `30m`, `2h`."""

import re


# == Contract =============================================================================

def parse_duration(text: str) -> float:
    """Seconds in a duration typed as digits and one unit: `45s`, `30m` or `2h`.

    Raises ValueError ("invalid duration: ...") for anything else: no unit, a sign, a
    fraction, spaces, upper case or a compound such as `1h30m`.
    """
    m = _DURATION_RE.fullmatch(text)
    if m is None:
        raise ValueError(f"invalid duration: {text}")
    return float(int(m.group(1)) * _DURATION_UNITS[m.group(2)])


# == Implementation =======================================================================

_DURATION_RE = re.compile(r"([0-9]+)([smh])")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}
