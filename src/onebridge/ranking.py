"""Closure ranking by total finite added travel time."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping

_ID = re.compile(r"[a-z0-9][a-z0-9._:-]*", re.ASCII)


def require_id(value: object, label: str) -> str:
    """Return ``value`` if it is a canonical lowercase identifier."""
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def require_number(value: object, label: str) -> float:
    """Return ``value`` as a float if it is finite and nonnegative."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return number


def rank_closures(rows: Iterable[Mapping]) -> list[dict]:
    """Sort closures by total added minutes and assign competition ranks.

    Equal totals share a rank (1, 2, 2, 4). The closure ID only orders ties for
    display; new disconnections are reported separately and never break ties.
    """
    ranked = []
    seen = set()
    for row in rows:
        cid = require_id(row.get("closure_id"), "closure_id")
        if cid in seen:
            raise ValueError(f"duplicate closure_id: {cid}")
        seen.add(cid)
        require_number(
            row.get("total_finite_added_minutes"), "total_finite_added_minutes"
        )
        ranked.append(dict(row))
    ranked.sort(key=lambda row: (-row["total_finite_added_minutes"], row["closure_id"]))
    previous = None
    rank = 0
    for position, row in enumerate(ranked, 1):
        total = row["total_finite_added_minutes"]
        if total != previous:
            rank = position
        row["rank"] = rank
        previous = total
    return ranked
