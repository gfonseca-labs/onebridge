"""Check tracked publication content and built archives without printing matches.

Hosted CI accounts are infrastructure, not developer-machine identifiers.
Findings contain relative filenames and rule names; never matched private text.
"""

from __future__ import annotations

import gzip
import io
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

RULES = {
    "personal path": re.compile(
        r"/(?:Users|home)/(?!runner(?:admin)?(?:/|\b)|vsts(?:/|\b))[^/\s\"']+"
        r"|[A-Z]:\\Users\\(?!runneradmin\\)[^\\\s\"']+"
        r"|/private/(?:var)/folders/[^\s\"']+"
    ),
    "private document link": re.compile(
        r"(?:docs|drive)\.google\.com|chatgpt" r"-conversation:|chatgpt\.com/(?:c|g)/"
    ),
}
PROSE = {
    "unexplained decision code": re.compile(r"\bD-\d{3}[A-Z]?\b"),
    "private preparation reference": re.compile(
        r"private documentation|internal (?:document|drive|planning|checklist)"
        r"|development history.{0,40}private|deliverables/",
        re.IGNORECASE,
    ),
    "unfinished instruction": re.compile(r"\bTODO\b|\bFIXME\b|git clone <repository>"),
}
LIMIT = 300 * 1024 * 1024


def inspect(data, name):
    if len(data) > LIMIT:
        return [(name, "inspection size limit")]
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as zipped:
            data = zipped.read(LIMIT + 1)
        if len(data) > LIMIT:
            return [(name, "decompressed size limit")]
    text = data.decode("utf-8", errors="replace")
    rules = RULES | (PROSE if Path(name).suffix in {".md", ".rst", ".txt"} else {})
    return [(name, label) for label, pattern in rules.items() if pattern.search(text)]


def archive_findings(path):
    findings = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    data = archive.read(entry) if entry.file_size <= LIMIT else b""
                    findings += (
                        inspect(data, entry.filename)
                        if entry.file_size <= LIMIT
                        else [(entry.filename, "inspection size limit")]
                    )
    else:
        with tarfile.open(path) as archive:
            for entry in archive:
                if entry.uid or entry.gid or entry.uname or entry.gname:
                    findings.append((entry.name, "archive ownership"))
                if entry.isfile():
                    findings += (
                        inspect(archive.extractfile(entry).read(), entry.name)
                        if entry.size <= LIMIT
                        else [(entry.name, "inspection size limit")]
                    )
    return findings


def main():
    names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    findings = []
    for name in filter(None, names):
        path = Path(name)
        if path.is_file():
            findings += inspect(path.read_bytes(), name)
    for name in sys.argv[1:]:
        findings += archive_findings(Path(name))
    for name, label in sorted(set(findings)):
        print(f"{name}: {label}")
    print(f"Publication check: {len(set(findings))} candidate findings")
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
