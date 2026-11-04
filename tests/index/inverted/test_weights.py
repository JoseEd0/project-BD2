"""Los pesos de SMART: el peso logarítmico y la norma lnc de un documento."""

import random

import pytest
from inverted_support import lnc_norm_by_definition

from index.inverted import lnc_norm

SEEDS = [5, 17, 29]
DOCUMENTS = 200
MAX_TERMS = 60
# Frecuencias pequeñas, como casi todas las de un corpus, y alguna enorme.
FREQUENCIES = [1, 1, 1, 2, 2, 3, 4, 7, 12, 40, 1_000, 123_457]


def test_a_document_without_terms_has_norm_zero():
    assert lnc_norm([]) == 0.0


@pytest.mark.parametrize("seed", SEEDS)
def test_the_norm_is_its_formula_bit_for_bit(seed: int):
    generator = random.Random(seed)
    for _ in range(DOCUMENTS):
        frequencies = generator.choices(FREQUENCIES, k=generator.randrange(1, MAX_TERMS))
        assert lnc_norm(frequencies) == lnc_norm_by_definition(frequencies)
        assert lnc_norm(iter(frequencies)) == lnc_norm_by_definition(frequencies)
