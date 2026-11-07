"""Publish a result directory atomically, never replacing an existing one.

Files are written to a hidden staging directory next to the destination,
validated, and then renamed into place with the kernel's no-replace rename
(``renamex_np(RENAME_EXCL)`` on macOS, ``renameat2(RENAME_NOREPLACE)`` on
Linux). Readers see either nothing or the complete, validated directory.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import shutil
import tempfile
from pathlib import Path

AT_FDCWD = -100
RENAME_EXCL = 4  # macOS
RENAME_NOREPLACE = 1  # Linux


def publish_bytes(destination, files: dict[str, bytes], validate) -> Path:
    """Write ``files`` to a new ``destination`` after ``validate(stage)`` passes."""
    destination = Path(destination)
    if not destination.name or destination.name in {".", ".."}:
        raise ValueError("destination must name a new directory")
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if os.path.lexists(destination):
        raise FileExistsError(
            errno.EEXIST, "destination already exists", str(destination)
        )
    for name, content in files.items():
        if (
            Path(name).name != name
            or name in {"", ".", ".."}
            or not isinstance(content, bytes)
        ):
            raise ValueError(f"invalid file entry {name!r}")
    stage = Path(tempfile.mkdtemp(prefix=".onebridge-stage-", dir=parent))
    try:
        for name, content in files.items():
            with (stage / name).open("xb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        validate(stage)
        _rename_no_replace(stage, destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _rename_no_replace(source: Path, destination: Path):
    libc = ctypes.CDLL(None, use_errno=True)
    system = platform.system()
    if system == "Darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), RENAME_EXCL)
    elif system == "Linux" and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            AT_FDCWD,
            os.fsencode(source),
            AT_FDCWD,
            os.fsencode(destination),
            RENAME_NOREPLACE,
        )
    else:
        raise OSError(errno.ENOTSUP, "no atomic no-replace rename on this platform")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))
