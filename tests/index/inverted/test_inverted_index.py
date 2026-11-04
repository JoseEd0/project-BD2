"""El índice invertido construido: lo que guarda, cómo se busca y cómo se abre."""

import math
from array import array
from collections.abc import Callable
from pathlib import Path

import pytest
from inverted_support import (
    ReferenceIndex,
    assert_matches_reference,
    build,
    random_texts,
    record_id_of,
)

from config import EngineConfig
from index.inverted import (
    DocumentNotFoundError,
    InvertedFormatError,
    InvertedIndex,
    InvertedIndexExistsError,
    InvertedIndexNotFoundError,
    TermTooLongError,
    max_term_bytes,
)
from index.inverted.format import (
    FIRST_GENERATION,
    POSTING_BYTES,
    POSTING_FIELDS,
    POSTING_TYPECODE,
    documents_path,
    lexicon_path,
    manifest_path,
    postings_path,
    segment_base,
    terms_path,
)
from text import Analyzer

# Un carácter fuera del plano básico: cuatro bytes en UTF-8, y una letra para el analizador.
FOUR_BYTE_LETTER = "\U00020000"
MAGIC_OFFSET = 0
VERSION_OFFSET = 4
FIRST_FIELD_OFFSET = 6


@pytest.fixture
def path(config: EngineConfig) -> Path:
    return config.data_directory / "productos.inv"


def in_first_segment(file_of: Callable[[Path], Path]) -> Callable[[Path], Path]:
    """El archivo del segmento que escribió la construcción."""
    return lambda path: file_of(segment_base(path, FIRST_GENERATION))


def test_a_posting_is_two_four_byte_integers():
    assert array(POSTING_TYPECODE).itemsize * POSTING_FIELDS == POSTING_BYTES


def test_an_empty_index(path: Path, config: EngineConfig):
    with build(path, [], config) as index:
        assert index.document_count == 0
        assert index.total_length == 0
        assert index.average_length == 0.0
        assert index.vocabulary_size == 0
        assert index.posting_count == 0
        assert index.block_count == 0
        assert index.merge_passes == 0
        assert list(index.terms()) == []
        assert list(index.documents()) == []
        assert len(index.postings("cas")) == 0
        assert index.document_frequency("cas") == 0
        assert index.lookup("cas") is None
        with pytest.raises(DocumentNotFoundError):
            index.document(0)


def test_one_document(path: Path, config: EngineConfig):
    with build(path, ["La casa, las casas y la casita"], config) as index:
        assert index.document_count == 1
        assert index.total_length == 3
        assert index.vocabulary_size == 2
        assert index.block_count == 1
        assert index.merge_passes == 0
        postings = index.postings("cas")
        assert list(postings.doc_ids) == [0]
        assert list(postings.term_frequencies) == [2]
        assert index.document_frequency("casit") == 1
        document = index.document(0)
        assert document.record_id == record_id_of(0)
        assert document.length == 3
        assert document.norm == pytest.approx(math.sqrt((1 + math.log10(2)) ** 2 + 1))
        assert not document.deleted


def test_a_document_of_only_stopwords_has_length_and_norm_zero(path: Path, config: EngineConfig):
    with build(path, ["de la y el", "teclado"], config) as index:
        assert index.document(0).length == 0
        assert index.document(0).norm == 0.0
        assert index.document(1).length == 1
        assert list(index.postings("tecl").doc_ids) == [1]


def test_a_term_present_in_every_document(path: Path, config: EngineConfig):
    texts = [f"teclado modelo{number}" for number in range(50)]
    with build(path, texts, config) as index:
        postings = index.postings("tecl")
        assert list(postings.doc_ids) == list(range(50))
        assert list(postings.term_frequencies) == [1] * 50
        assert index.document_frequency("tecl") == index.document_count


def test_a_posting_list_that_crosses_pages(path: Path, config: EngineConfig):
    count = 3 * config.page_size // POSTING_BYTES + 5
    texts = [" ".join(["cable"] * (1 + number % 4)) for number in range(count)]
    with build(path, texts, config) as index:
        postings = index.postings("cabl")
        assert len(postings) == count
        assert list(postings.doc_ids) == list(range(count))
        assert list(postings.term_frequencies) == [1 + number % 4 for number in range(count)]
        assert index.describe()["file_bytes"]["postings"] >= 4 * config.page_size


def test_terms_of_the_maximum_length(path: Path, config: EngineConfig):
    longest = FOUR_BYTE_LETTER * config.text_max_token_length
    assert Analyzer(config).terms(longest) == [longest]
    assert len(longest.encode()) == max_term_bytes(config)
    texts = [f"{longest} cable", "cable", longest]
    with build(path, texts, config) as index:
        assert index.describe()["term_bytes"] == len(longest.encode()) + len("cabl")
        assert list(index.postings(longest).doc_ids) == [0, 2]
        assert list(index.postings("cabl").doc_ids) == [0, 1]
        assert index.lookup(longest + FOUR_BYTE_LETTER) is None


def test_a_term_longer_than_the_lexicon_admits_is_rejected(path: Path, config: EngineConfig):
    generous = EngineConfig(
        page_size=config.page_size,
        buffer_pool_pages=config.buffer_pool_pages,
        data_directory=config.data_directory,
        text_max_token_length=config.text_max_token_length * 2,
    )
    too_long = FOUR_BYTE_LETTER * (config.text_max_token_length + 1)
    with pytest.raises(TermTooLongError):
        InvertedIndex.build(path, iter([(record_id_of(0), too_long)]), Analyzer(generous), config)
    assert not any(file.exists() for file in InvertedIndex.files_of(path))


def test_unicode_terms_are_kept_apart_and_found(path: Path, config: EngineConfig):
    texts = ["año ñandú", "ano", "año", "nandu"]
    with build(path, texts, config) as index:
        assert list(index.postings("año").doc_ids) == [0, 2]
        assert list(index.postings("ano").doc_ids) == [1]
        assert list(index.postings("ñandu").doc_ids) == [0]
        assert list(index.postings("nandu").doc_ids) == [3]
        terms = [entry.term for entry in index.terms()]
        assert terms == sorted(terms)


def test_lookups_before_after_and_between_the_terms(path: Path, config: EngineConfig):
    texts = [f"modelo{number:04d}" for number in range(300)]
    with build(path, texts, config) as index:
        assert index.describe()["lexicon_pages"] > 2
        for entry in index.terms():
            assert index.lookup(entry.term) == entry
        for absent in ["", "a", "zzzz", "modelo", "modelo00005", "model0150x"]:
            assert index.lookup(absent) is None


def test_the_index_matches_a_reference_built_in_memory(path: Path, config: EngineConfig):
    texts = [
        "Cable USB-C de 3,5 mm para iPhone12",
        "Teclado inalámbrico con ñ, teclado y ratón",
        "Funda de cuero para teléfono; funda roja",
        "",
        "Cargador rápido USB 128GB, cargador",
    ]
    with build(path, texts, config) as index:
        assert_matches_reference(index, ReferenceIndex.of(texts, Analyzer(config)))


def test_reopening_gives_the_same_index(path: Path, config: EngineConfig):
    texts = [f"cable {number} funda teclado{number % 7}" for number in range(120)]
    with build(path, texts, config) as index:
        terms = list(index.terms())
        documents = list(index.documents())
        description = index.describe()
    with InvertedIndex(path, config) as reopened:
        assert list(reopened.terms()) == terms
        assert list(reopened.documents()) == documents
        assert reopened.describe() == description
        assert reopened.language == "es"
        assert_matches_reference(reopened, ReferenceIndex.of(texts, Analyzer(config)))


def test_describe_reports_the_shape_and_the_most_frequent_terms(path: Path, config: EngineConfig):
    texts = ["cable funda", "cable teclado", "cable funda raton", "camara"]
    with build(path, texts, config) as index:
        description = index.describe(top_terms=3)
    assert description["kind"] == "inverted"
    assert description["documents"] == 4
    assert description["vocabulary"] == 5
    assert description["postings"] == 8
    assert description["blocks"] == 1
    assert description["average_length"] == 2.0
    assert [term["term"] for term in description["top_terms"]] == ["cabl", "fund", "cam"]
    assert description["top_terms"][0] == {
        "term": "cabl",
        "document_frequency": 3,
        "total_frequency": 3,
    }


def test_documents_are_reached_by_doc_id(path: Path, config: EngineConfig):
    texts = [f"teclado {'cable ' * number}" for number in range(100)]
    with build(path, texts, config) as index:
        assert index.document(57).record_id == record_id_of(57)
        assert index.document(57).length == 58
        with pytest.raises(DocumentNotFoundError):
            index.document(100)
        with pytest.raises(DocumentNotFoundError):
            index.document(-1)


def test_files_of_names_every_file_and_the_build_leaves_nothing_else(
    path: Path, config: EngineConfig
):
    assert [file.name for file in InvertedIndex.files_of(path)] == [
        "productos.inv.documents",
        "productos.inv.manifest",
        "productos.inv.docmap",
        "productos.inv.docmap.dir",
    ]
    build(path, [f"cable {number}" for number in range(30)], config).close()
    files = InvertedIndex.files_of(path)
    assert [file.name for file in files][4:] == [
        "productos.inv.seg0.lexicon",
        "productos.inv.seg0.terms",
        "productos.inv.seg0.postings",
    ]
    assert sorted(path.parent.iterdir()) == sorted(files)


def test_building_over_an_existing_index_is_refused(path: Path, config: EngineConfig):
    build(path, ["cable"], config).close()
    with pytest.raises(InvertedIndexExistsError):
        build(path, ["funda"], config)
    with InvertedIndex(path, config) as index:
        assert index.document_frequency("cabl") == 1


def test_opening_a_missing_index_is_an_error(path: Path, config: EngineConfig):
    with pytest.raises(InvertedIndexNotFoundError):
        InvertedIndex(path, config)
    assert not any(file.exists() for file in InvertedIndex.files_of(path))


def patch_header(file: Path, offset: int, replacement: bytes) -> None:
    raw = bytearray(file.read_bytes())
    raw[offset : offset + len(replacement)] = replacement
    file.write_bytes(bytes(raw))


@pytest.mark.parametrize(
    ("file_of", "offset", "replacement", "message"),
    [
        (in_first_segment(lexicon_path), MAGIC_OFFSET, b"XXXX", "no es un léxico"),
        (in_first_segment(lexicon_path), VERSION_OFFSET, (99).to_bytes(2, "little"), "versión 99"),
        (in_first_segment(terms_path), MAGIC_OFFSET, b"IVLX", "no es un archivo de términos"),
        (in_first_segment(postings_path), MAGIC_OFFSET, b"IVDC", "no es un archivo de postings"),
        (documents_path, MAGIC_OFFSET, b"IVPS", "no es una tabla de documentos"),
        (manifest_path, MAGIC_OFFSET, b"IVDC", "no es un manifiesto"),
        (manifest_path, VERSION_OFFSET, (99).to_bytes(2, "little"), "versión 99"),
    ],
)
def test_opening_a_file_of_another_format_is_an_error(
    path: Path,
    config: EngineConfig,
    file_of: Callable[[Path], Path],
    offset: int,
    replacement: bytes,
    message: str,
):
    build(path, ["cable"], config).close()
    patch_header(file_of(path), offset, replacement)
    with pytest.raises(InvertedFormatError, match=message):
        InvertedIndex(path, config)


def test_files_of_different_indexes_are_not_mixed(path: Path, config: EngineConfig):
    build(path, ["cable funda"], config).close()
    patch_header(in_first_segment(postings_path)(path), FIRST_FIELD_OFFSET, (7).to_bytes(8, "little"))
    with pytest.raises(InvertedFormatError, match="postings"):
        InvertedIndex(path, config)


def test_a_terms_file_of_another_index_is_detected(path: Path, config: EngineConfig):
    build(path, ["cable funda"], config).close()
    patch_header(
        in_first_segment(terms_path)(path), FIRST_FIELD_OFFSET, (999).to_bytes(8, "little")
    )
    with pytest.raises(InvertedFormatError, match="bytes de términos"):
        InvertedIndex(path, config)


def test_document_columns_hold_every_length_norm_and_deletion(path: Path, config: EngineConfig):
    texts = random_texts(300, seed=5)
    with build(path, texts, config) as index:
        for position in [0, 117, 299]:
            index.delete(record_id_of(position))
    with InvertedIndex(path, config) as index:
        columns = index.document_columns()
        documents = list(index.documents())
        assert list(columns.lengths) == [document.length for document in documents]
        assert list(columns.norms) == [document.norm for document in documents]
        assert [columns.record_id(doc_id) for doc_id in range(len(texts))] == [
            document.record_id for document in documents
        ]
        assert columns.deleted == {0, 117, 299}
        assert index.document_columns() is columns


def test_document_columns_of_an_empty_index(path: Path, config: EngineConfig):
    with build(path, [], config) as index:
        columns = index.document_columns()
        assert len(columns.lengths) == len(columns.norms) == len(columns.record_keys) == 0
        assert columns.deleted == set()
