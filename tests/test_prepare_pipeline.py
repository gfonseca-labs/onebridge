import gzip
import json
import time

import pytest

from onebridge.cli import main
from onebridge.prepare.output import write_json, write_lines


def test_bundle_files_are_byte_reproducible(tmp_path):
    rows = [{"b": 1, "a": [1, 2]}, {"a": None}]
    write_lines(tmp_path / "one.jsonl.gz", rows)
    time.sleep(1.1)  # a gzip timestamp would change after a second
    write_lines(tmp_path / "two.jsonl.gz", rows)
    first = (tmp_path / "one.jsonl.gz").read_bytes()
    assert first == (tmp_path / "two.jsonl.gz").read_bytes()
    assert gzip.decompress(first).decode() == '{"a": [1, 2], "b": 1}\n{"a": null}\n'
    write_json(tmp_path / "value.json.gz", {"2": [0.5, 1.5]})
    with gzip.open(tmp_path / "value.json.gz", "rt") as f:
        assert json.load(f) == {"2": [0.5, 1.5]}


def test_prepare_requires_every_input(capsys):
    with pytest.raises(SystemExit):
        main(["prepare", "--config", "c.toml", "--extract", "e", "--out", "o"])
    assert "--sources" in capsys.readouterr().err
