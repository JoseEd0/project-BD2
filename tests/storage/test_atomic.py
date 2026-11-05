"""Un archivo se reescribe entero o se queda como estaba."""

import pytest

from storage.atomic import staged_path, write_atomically


def test_the_new_content_replaces_the_old(tmp_path):
    path = tmp_path / "manifiesto"
    path.write_bytes(b"viejo")

    write_atomically(path, lambda file: file.write(b"nuevo"))

    assert path.read_bytes() == b"nuevo"
    assert list(tmp_path.iterdir()) == [path]


def test_a_failed_write_leaves_the_old_file_and_nothing_else(tmp_path):
    path = tmp_path / "manifiesto"
    path.write_bytes(b"viejo")

    def fail_halfway(file):
        file.write(b"a medias")
        raise OSError("disco lleno")

    with pytest.raises(OSError, match="disco lleno"):
        write_atomically(path, fail_halfway)

    assert path.read_bytes() == b"viejo"
    assert not staged_path(path).exists()
