import hashlib

import pytest

from onebridge.prepare.downloads import fetched


def test_downloads_are_checked_against_the_configuration(tmp_path):
    (tmp_path / "blocks.zip").write_bytes(b"data")
    config = {"downloads": {"blocks.zip": hashlib.sha256(b"data").hexdigest()}}
    assert fetched(tmp_path, "blocks.zip", config) == tmp_path / "blocks.zip"
    config["downloads"]["blocks.zip"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        fetched(tmp_path, "blocks.zip", config)
    with pytest.raises(ValueError, match="not listed"):
        fetched(tmp_path, "other.zip", config)
    config["downloads"]["missing.zip"] = "0" * 64
    with pytest.raises(FileNotFoundError, match="DATA.md"):
        fetched(tmp_path, "missing.zip", config)
