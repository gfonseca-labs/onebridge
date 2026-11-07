"""Reproducible output files.

JSON is written with sorted keys (JSON lines) and gzip headers carry no
timestamp or file name, so the same data always gives the same bytes.
"""

from __future__ import annotations

import gzip
import json


def write_lines(path, rows):
    """Gzipped JSON lines with sorted keys."""
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    write_gzip(path, text.encode())


def write_json(path, value):
    """One gzipped JSON value."""
    write_gzip(path, json.dumps(value).encode())


def write_gzip(path, data):
    with (
        open(path, "wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f,
    ):
        f.write(data)
