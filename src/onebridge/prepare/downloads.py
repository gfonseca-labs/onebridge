"""Downloaded source files, pinned by SHA-256 in the configuration.

``config["downloads"]`` maps each file name to its SHA-256. The study only
reads a downloaded file after its digest matches, so a changed or partial
download fails loudly instead of quietly changing the results. URLs are
listed in docs/DATA.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def fetched(folder, name, config):
    """Path of the downloaded file ``name`` in ``folder``, checked."""
    pinned = config["downloads"].get(name)
    if pinned is None:
        raise ValueError(f"{name} is not listed under [downloads]")
    path = Path(folder) / name
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing; see docs/DATA.md")
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != pinned:
        raise ValueError(f"{path} does not match its pinned SHA-256")
    return path
