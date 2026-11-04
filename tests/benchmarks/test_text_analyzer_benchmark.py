"""La medida de velocidad del analizador de texto."""

from pathlib import Path

from benchmarks import text_analyzer_benchmark

REPETITIONS = 2
DOCUMENTS = ["Auriculares inalámbricos con cancelación de ruido", "la de los", "Cable USB-C 2m"]


def test_blank_lines_are_not_documents(tmp_path: Path):
    source = tmp_path / "corpus.txt"
    source.write_text("primera línea\n\n   \nsegunda línea\n", encoding="utf-8")
    assert text_analyzer_benchmark.read_documents([source]) == ["primera línea", "segunda línea"]


def test_the_experiment_measures_a_cold_and_a_warm_pass():
    report = text_analyzer_benchmark.run(DOCUMENTS, REPETITIONS, "es")
    assert report.parameters["documentos"] == len(DOCUMENTS)
    assert report.parameters["tokens"] == 14
    assert 0 < report.parameters["términos"] < report.parameters["tokens"]
    assert [item.operation for item in report.measurements] == [
        text_analyzer_benchmark.COLD_PASS,
        text_analyzer_benchmark.WARM_PASS,
    ]
    assert all(item.extra["tokens_por_segundo"] > 0 for item in report.measurements)
