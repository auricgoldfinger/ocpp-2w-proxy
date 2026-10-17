"""Short human-readable durations for log lines and link notes."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """``1.8s`` below ten seconds, else whole units: ``42s``, ``7m01s``, ``2h03m``, as the monitor UI writes them."""
    if seconds < 10:
        return f"{max(seconds, 0):.1f}s"
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"
