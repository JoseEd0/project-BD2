from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.corpus import main
from rag.settings import DATA_DIR_VARIABLE


def test_build_without_a_data_directory_explains_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(DATA_DIR_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as stopped:
        main(["build"])
    assert stopped.value.code == 1
    assert DATA_DIR_VARIABLE in capsys.readouterr().err


def test_figure_rejects_a_file_that_is_not_a_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("matplotlib")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"counts": {}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["figure", "--manifest", str(manifest), "--output", str(tmp_path / "f.png")])
    assert "chunk_words" in capsys.readouterr().err


def test_figure_draws_the_histogram_of_a_manifest(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    manifest = {
        "counts": {"papers": 2},
        "parameters": {"chunks": {"min_words": 200, "target_words": 300, "max_words": 500}},
        "chunk_words": {
            "count": 3,
            "min": 210,
            "p50": 290,
            "p95": 300,
            "max": 300,
            "histogram": {"bin_words": 20, "counts": [0] * 10 + [1] + [0] * 3 + [2] + [0] * 10},
        },
    }
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "figures" / "lengths.png"
    main(["figure", "--manifest", str(source), "--output", str(output)])
    assert output.read_bytes().startswith(b"\x89PNG")
