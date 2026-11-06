"""Los descriptores de la muestra se guardan una vez y se leen tal cual."""

import numpy as np
import pytest

from index.similarity.spill import SPILL_SUFFIX, DescriptorSpill
from storage.record_id import RecordId

DIMENSION = 39


def test_descriptors_come_back_as_they_were_kept(tmp_path):
    rng = np.random.default_rng(0)
    kept = {
        RecordId(1, slot): rng.standard_normal((rows, DIMENSION)).astype(np.float32)
        for slot, rows in enumerate((5, 0, 300))
    }

    with DescriptorSpill(tmp_path / "canciones.i.idx") as spill:
        for record_id, descriptors in kept.items():
            assert spill.keep(record_id, descriptors) is not None
        for record_id, descriptors in reversed(kept.items()):
            assert record_id in spill
            assert np.array_equal(spill.read(record_id), descriptors)
            assert spill.read(record_id).shape == descriptors.shape
        assert RecordId(9, 9) not in spill


def test_the_temporary_file_is_removed_even_after_a_failure(tmp_path):
    path = tmp_path / "fotos.i.idx"

    with pytest.raises(RuntimeError), DescriptorSpill(path) as spill:
        spill.keep(RecordId(1, 0), np.ones((2, DIMENSION), dtype=np.float32))
        assert path.with_name(path.name + SPILL_SUFFIX).exists()
        raise RuntimeError("la extracción falló")

    assert list(tmp_path.iterdir()) == []
