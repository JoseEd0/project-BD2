"""El analizador: el mismo texto da siempre los mismos términos, venga de un documento o de
una consulta."""

import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from config import EngineConfig
from text import Analyzer, UnknownLanguageError

THREADS = 8
DISTINCT_WORDS = 400
ALPHABET = 26
# Cambiar de hilo cada microsegundo provoca que dos hilos coincidan dentro del stemmer.
TIGHT_SWITCH_INTERVAL = 1e-6


@pytest.fixture(scope="module")
def spanish() -> Analyzer:
    return Analyzer(EngineConfig())


@pytest.fixture(scope="module")
def english() -> Analyzer:
    return Analyzer(EngineConfig(), "en")


def test_spanish_is_the_default_language(spanish: Analyzer):
    assert spanish.language == "es"
    assert Analyzer(EngineConfig(), " EN ").language == "en"


def test_an_unknown_language_is_rejected_when_the_analyzer_is_created():
    with pytest.raises(UnknownLanguageError, match="'klingon'"):
        Analyzer(EngineConfig(), "klingon")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("canción", "cancion"),
        ("baterías", "bateria"),
        ("TELÉFONO Inalámbrico", "telefono inalambrico"),
        ("¡Hola, mundo!", "hola mundo"),
        ("rápido; (barato) / fácil...", "rapido barato facil"),
        ("3,5 mm", "3.5mm"),
        ("128GB", "128 gb"),
        ("iPhone12", "iphone 12"),
        ("L'Oréal", "loreal"),
    ],
)
def test_spellings_of_the_same_words_give_the_same_terms(
    spanish: Analyzer, first: str, second: str
):
    assert spanish.terms(first) == spanish.terms(second)


def test_the_eñe_is_kept(spanish: Analyzer):
    assert spanish.terms("año") == ["año"]
    assert spanish.terms("año") != spanish.terms("ano")


@pytest.mark.parametrize(
    ("text", "terms"),
    [
        ("3,5", ["3.5"]),
        ("3.5", ["3.5"]),
        ("1.000", ["1.000"]),
        ("modelo 2020.", ["model", "2020"]),
        ("4K", ["4", "k"]),
        ("usb-c", ["usb", "c"]),
        ("wi-fi", ["wi", "fi"]),
        ("talla M", ["tall", "m"]),
        ("sm_g991b", ["sm", "g", "991", "b"]),
    ],
)
def test_numbers_hyphens_and_model_codes(spanish: Analyzer, text: str, terms: list[str]):
    assert spanish.terms(text) == terms


def test_stopwords_are_removed_before_stemming_and_without_accents(spanish: Analyzer):
    """«nosotros» se reduciría a «nosotr», que ya no está en la lista."""
    assert spanish.terms("Nosotros tenemos MÁS casas que Él") == ["cas"]


def test_spanish_words_are_reduced_to_their_root(spanish: Analyzer):
    assert spanish.terms("corriendo computadoras computadora") == ["corr", "comput", "comput"]


def test_english_words_are_reduced_to_their_root(english: Analyzer):
    assert english.terms("The databases are running") == ["databas", "run"]


def test_each_language_removes_its_own_stopwords(spanish: Analyzer, english: Analyzer):
    assert english.terms("the") == []
    assert spanish.terms("the") == ["the"]


@pytest.mark.parametrize("text", ["", "   ", "¡¿...?! — / -"])
def test_text_without_tokens_has_no_terms(spanish: Analyzer, text: str):
    assert spanish.terms(text) == []
    analyzed = spanish.analyze(text)
    assert analyzed.length == 0
    assert not analyzed.frequencies


def test_a_text_of_only_stopwords_has_no_terms(spanish: Analyzer):
    assert spanish.terms("de la y los que en el por") == []


def test_tokens_outside_the_configured_lengths_are_dropped():
    analyzer = Analyzer(EngineConfig(text_min_token_length=2, text_max_token_length=6))
    assert analyzer.terms("usb-c 4K 12345678 cable supercalifragilistico") == [
        "usb",
        "cabl",
    ]


def test_analyze_counts_each_term_and_the_length(spanish: Analyzer):
    analyzed = spanish.analyze("La casa, las casas y la casita")
    assert analyzed.frequencies == {"cas": 2, "casit": 1}
    assert analyzed.length == 3


def test_documents_and_queries_are_analyzed_identically(spanish: Analyzer):
    document = "Auriculares inalámbricos Bluetooth 5.0 con cancelación de ruido"
    query = "AURICULARES inalambricos con cancelacion"
    analyzed = spanish.analyze(document)
    assert analyzed.frequencies == Counter(spanish.terms(document))
    assert analyzed.length == len(spanish.terms(document))
    assert set(spanish.terms(query)) <= set(analyzed.frequencies)


def test_the_analysis_is_deterministic(spanish: Analyzer):
    text = "Zapatillas de running para mujer, talla 38, color negro 2x1"
    assert spanish.terms(text) == spanish.terms(text)
    assert Analyzer(EngineConfig()).terms(text) == spanish.terms(text)


def distinct_words(count: int) -> list[str]:
    """Palabras distintas, solo de letras, que el stemmer tiene que recortar."""
    return [
        "".join(chr(ord("a") + index // ALPHABET**place % ALPHABET) for place in range(3)) + suffix
        for index in range(count)
        for suffix in ("aciones", "amente", "ería")
    ]


def terms_from_threads(shared: Analyzer, text: str) -> list[list[str]]:
    """Los términos del texto, pedidos a la vez por `THREADS` hilos al mismo analizador."""
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(TIGHT_SWITCH_INTERVAL)
    try:
        with ThreadPoolExecutor(THREADS) as pool:
            return list(pool.map(lambda _: shared.terms(text), range(THREADS)))
    finally:
        sys.setswitchinterval(previous_interval)


def test_concurrent_threads_get_the_same_terms_as_one_thread():
    text = " ".join(distinct_words(DISTINCT_WORDS))
    expected = Analyzer(EngineConfig()).terms(text)
    results = terms_from_threads(Analyzer(EngineConfig()), text)
    assert all(result == expected for result in results)


TINY_GENERATION = 5
REPEATED_TEXT = (
    "Zapatillas de running para mujer, talla 38 y 3,5 cm: zapatillas ligeras de running, "
    "talla 38, color negro; las zapatillas de mujer más vendidas"
)


def test_a_tiny_cache_that_rotates_gives_the_same_terms(monkeypatch: pytest.MonkeyPatch):
    words = " ".join(distinct_words(DISTINCT_WORDS))
    expected = Analyzer(EngineConfig()).terms(REPEATED_TEXT + " " + words)
    monkeypatch.setattr("text.analyzer.TERM_CACHE_GENERATION_ENTRIES", TINY_GENERATION)
    rotating = Analyzer(EngineConfig())
    assert rotating.terms(REPEATED_TEXT + " " + words) == expected
    assert rotating.terms(REPEATED_TEXT + " " + words) == expected


def test_the_cache_keeps_at_most_two_generations(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("text.analyzer.TERM_CACHE_GENERATION_ENTRIES", TINY_GENERATION)
    analyzer = Analyzer(EngineConfig())
    analyzer.terms(" ".join(distinct_words(DISTINCT_WORDS)))
    cached = len(analyzer._recent_terms) + len(analyzer._previous_terms)
    assert cached <= 2 * TINY_GENERATION


def test_concurrent_threads_agree_while_the_cache_rotates(monkeypatch: pytest.MonkeyPatch):
    text = " ".join(distinct_words(DISTINCT_WORDS))
    expected = Analyzer(EngineConfig()).terms(text)
    monkeypatch.setattr("text.analyzer.TERM_CACHE_GENERATION_ENTRIES", TINY_GENERATION)
    results = terms_from_threads(Analyzer(EngineConfig()), text)
    assert all(result == expected for result in results)
