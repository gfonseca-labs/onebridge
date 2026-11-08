import gzip
import importlib.util
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "publication", ROOT / "tools/check_publication.py"
)
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


def test_private_paths_and_links_are_reported_without_their_values():
    path = "/" + "Users" + "/example-person/file"
    url = "https://" + "docs" + ".google.com/document/d/example"
    findings = publication.inspect((path + " " + url).encode(), "README.md")
    assert {label for _, label in findings} == {
        "personal path",
        "private document link",
    }
    assert "example-person" not in repr(findings)
    assert publication.inspect(b"/home/runner/work/project", "README.md") == []


def test_compressed_content_is_checked_without_erasing_source_annotations():
    private = ("/" + "home" + "/example-person/data").encode()
    assert publication.inspect(gzip.compress(private), "roads.json.gz")
    assert publication.inspect(b'{"fixme": "resurvey"}', "roads.json") == []
    assert publication.inspect(("D-" + "030").encode(), "README.md")


def test_release_builder_normalizes_source_metadata(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "builder", ROOT / "tools/build_release.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source, target = tmp_path / "source.tar.gz", tmp_path / "target.tar.gz"
    content = b"example contents"
    with tarfile.open(source, "w:gz") as archive:
        entry = tarfile.TarInfo("example/README.md")
        entry.uid = entry.gid = 123
        entry.uname = entry.gname = "example-owner"
        entry.mtime = 12345
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))
    builder.normalize_source(source, target)
    assert target.read_bytes()[3] == 0
    assert int.from_bytes(target.read_bytes()[4:8], "little") == 0
    with tarfile.open(target) as archive:
        entry = archive.getmembers()[0]
        assert (entry.uid, entry.gid, entry.uname, entry.gname, entry.mtime) == (
            0,
            0,
            "",
            "",
            0,
        )
        assert archive.extractfile(entry).read() == content
    assert publication.archive_findings(target) == []
