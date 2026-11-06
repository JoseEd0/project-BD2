"""Figuras del experimento multimedia: IVF vs HNSW, euclidiana vs coseno, con FAISS,
USearch y pgvector."""

from __future__ import annotations

from benchmarks.multimedia.names import (
    AUDIO,
    BUILD,
    CREATE_INDEX,
    CURVE,
    ENGINE_HNSW,
    ENGINE_IVF,
    EXACT,
    EXTRACTION_STAGE,
    FAISS_HNSW,
    FAISS_HNSW_WITHOUT_ZEROS,
    FAISS_IVF,
    FEATURES,
    GALLERY,
    IMAGES,
    MEMORY,
    PGVECTOR_HNSW,
    PGVECTOR_IVF,
    QUANTIZATION_STAGE,
    QUERY,
    QUERY_EXTRACTION_STAGE,
    RANDOM_NEIGHBOURS,
    RAW_VECTORS,
    READING_STAGE,
    SAMPLE_STAGE,
    SQL_HNSW,
    SQL_IVF,
    USEARCH_HNSW,
    VOCABULARY_STAGE,
    dataset_operation,
    metric_operation,
)
from benchmarks.plots.model import (
    MEMORY as MEMORY_MEASURE,
)
from benchmarks.plots.model import (
    READING_GUIDE,
    TOTAL_TIME,
    BarFigure,
    BarGroup,
    BarPanel,
    CurveFigure,
    CurvePanel,
    Figure,
    GalleryFigure,
    Line,
    Measure,
    Panel,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, AXIS, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE
from index.vector import VectorMetric

EXACT_STYLE = Technique(EXACT, Style(MUTED_INK, "x", dashed=True), "búsqueda exacta")
IVF = Technique(ENGINE_IVF, Style(BLUE, "o"))
HNSW = Technique(ENGINE_HNSW, Style(ORANGE, "s"))
USEARCH = Technique(USEARCH_HNSW, Style(AQUA, "^"))
PG_IVF = Technique(PGVECTOR_IVF, Style(BLUE, "D", dashed=True))
PG_HNSW = Technique(PGVECTOR_HNSW, Style(ORANGE, "v", dashed=True))
SQL = Technique(SQL_HNSW, Style(DARK_BLUE, "P"), "HNSW (gestor, por SQL)")
RANDOM = Technique(RANDOM_NEIGHBOURS, Style(AXIS, "x"))
WITHOUT_ZEROS = Technique(
    FAISS_HNSW_WITHOUT_ZEROS, Style(DARK_BLUE, "P", dashed=True), "HNSW sin vectores cero (FAISS)"
)
INDEXES = (IVF, HNSW, USEARCH, PG_IVF, PG_HNSW)
WITH_EXACT = (EXACT_STYLE, *INDEXES)
DATASETS = (IMAGES, AUDIO)
METRICS = (VectorMetric.EUCLIDEAN, VectorMetric.COSINE, VectorMetric.DOT)
METRIC_NAMES = {
    VectorMetric.EUCLIDEAN: "Euclidiana",
    VectorMetric.COSINE: "Coseno",
    VectorMetric.DOT: "Producto punto",
}
OBJECTS = "número de objetos de la colección"
QUERY_MEDIAN = Measure("mediana por consulta (ms)", "ms")
SIZES_LABEL = "objetos"

STAGES = (
    (EXTRACTION_STAGE, Style(BLUE, "o")),
    (SAMPLE_STAGE, Style(LIGHT_BLUE, "D")),
    (VOCABULARY_STAGE, Style(AQUA, "^")),
    (QUANTIZATION_STAGE, Style(ORANGE, "s")),
    (READING_STAGE, Style(MUTED_INK, "x", dashed=True)),
)
LAYERS = (
    ("FAISS directo", Style(BLUE, "o")),
    ("índice del gestor", Style(ORANGE, "s")),
    ("SQL (SIMILAR_TO)", Style(DARK_BLUE, "P")),
    ("extraer la consulta", Style(MUTED_INK, "x", dashed=True)),
)
LAYER_TECHNIQUES = {
    "IVF": (FAISS_IVF, ENGINE_IVF, SQL_IVF),
    "HNSW": (FAISS_HNSW, ENGINE_HNSW, SQL_HNSW),
}


def _query_panel(dataset: str, metric: VectorMetric) -> Panel:
    techniques = (*WITH_EXACT, SQL) if metric is VectorMetric.COSINE else WITH_EXACT
    return compare(
        metric_operation(dataset, metric, QUERY),
        f"{dataset.capitalize()} · {METRIC_NAMES[metric].lower()}",
        QUERY_MEDIAN,
        techniques,
    )


def _stages_panel(dataset: str) -> Panel:
    operation = dataset_operation(dataset, FEATURES)
    lines = [Line(name, style, name, operation) for name, style in STAGES]
    lines.append(
        Line(
            "CREATE INDEX completo (SQL)",
            Style(DARK_BLUE, "P"),
            SQL_HNSW,
            dataset_operation(dataset, CREATE_INDEX),
        )
    )
    return Panel(f"Características de {dataset}", TOTAL_TIME, tuple(lines))


def _layers_panel(dataset: str, method: str) -> Panel:
    operation = metric_operation(dataset, VectorMetric.COSINE, QUERY)
    lines = [
        Line(label, style, technique, operation)
        for (label, style), technique in zip(LAYERS, LAYER_TECHNIQUES[method], strict=False)
    ]
    label, style = LAYERS[-1]
    lines.append(Line(label, style, QUERY_EXTRACTION_STAGE, dataset_operation(dataset, QUERY)))
    return Panel(f"{method} · {dataset}", QUERY_MEDIAN, tuple(lines))


def _memory_panel(dataset: str) -> Panel:
    operation = metric_operation(dataset, VectorMetric.COSINE, MEMORY)
    in_memory = (
        Technique(FAISS_HNSW, HNSW.style, HNSW.name),
        USEARCH,
        PG_HNSW,
        Technique(RAW_VECTORS, Style(MUTED_INK, "D")),
    )
    return compare(
        operation, f"Memoria del HNSW · {dataset}", MEMORY_MEASURE, in_memory, field="memoria_kib"
    )


def _bar_groups(dataset: str) -> tuple[BarGroup, ...]:
    return tuple(
        BarGroup(METRIC_NAMES[metric].lower(), metric_operation(dataset, metric, QUERY))
        for metric in METRICS
    )


def _gallery(dataset: str, filename: str, images: bool, what: str) -> GalleryFigure:
    return GalleryFigure(
        filename=filename,
        report="multimedia",
        title=f"Galería de {dataset}: aciertos y errores del HNSW del gestor (coseno)",
        subtitle=(
            f"Cada fila es una consulta y sus 5 primeros vecinos entre 100 000 {what};\n"
            "el porcentaje es de los 10 primeros. Arriba, las consultas con más\n"
            "vecinos de su clase, cada una de otra clase; abajo, las que menos."
        ),
        technique=ENGINE_HNSW,
        operation=dataset_operation(dataset, GALLERY),
        cases=(("exitos", "Acierto"), ("errores", "Error")),
        images=images,
    )


FIGURES: tuple[Figure | BarFigure | CurveFigure | GalleryFigure, ...] = (
    Figure(
        filename="multimedia-consulta",
        report="multimedia",
        title="Búsqueda por similitud: tiempo por consulta",
        subtitle=(
            "Mediana de 500 consultas con el esfuerzo por omisión (IVF √listas, HNSW ef 40), "
            "top-10. pgvector incluye el viaje al servidor.\n"
            f"{READING_GUIDE}"
        ),
        rows_label=OBJECTS,
        columns=3,
        legend_columns=4,
        panels=tuple(_query_panel(dataset, metric) for dataset in DATASETS for metric in METRICS),
    ),
    CurveFigure(
        filename="multimedia-curvas",
        report="multimedia",
        title="Recall@10 contra latencia, barriendo el esfuerzo de búsqueda",
        subtitle=(
            "IVF: probes de 1 a 128; HNSW: ef_search de 10 a 320. El anillo marca el esfuerzo "
            "por omisión.\nArriba a la izquierda es mejor: más recall en menos tiempo."
        ),
        columns=3,
        x_axis="mediana por consulta (ms)",
        y_axis="Recall@10",
        field="recall_en_k",
        order_field="esfuerzo",
        default_field="por_omision",
        sizes_label=SIZES_LABEL,
        legend_columns=4,
        panels=tuple(
            CurvePanel(
                f"{dataset.capitalize()} · {METRIC_NAMES[metric].lower()}",
                metric_operation(dataset, metric, CURVE),
                (*INDEXES, WITHOUT_ZEROS),
            )
            for dataset in DATASETS
            for metric in METRICS
        ),
    ),
    BarFigure(
        filename="multimedia-recall",
        report="multimedia",
        title="Recall@10 contra la búsqueda exacta",
        subtitle=(
            "Media de 500 consultas con el esfuerzo por omisión. Más alto es mejor; 1 es "
            "encontrar los mismos 10 vecinos que la búsqueda exacta."
        ),
        sizes_label=SIZES_LABEL,
        panels=tuple(
            BarPanel(
                f"Recall@{{k}} · {dataset}",
                "Recall@10",
                "recall_en_k",
                _bar_groups(dataset),
                INDEXES,
            )
            for dataset in DATASETS
        ),
    ),
    BarFigure(
        filename="multimedia-categoria",
        report="multimedia",
        title="¿Los vecinos son de la clase de la consulta? Euclidiana vs coseno",
        subtitle=(
            "Parte de los 10 vecinos con la categoría (ABO) o la palabra (Speech Commands) de "
            "la consulta, media de 500.\nLa búsqueda exacta mide la representación; «al azar» "
            "es la proporción de la clase en la colección."
        ),
        sizes_label=SIZES_LABEL,
        panels=tuple(
            BarPanel(
                f"Vecinos de su clase · {dataset}",
                "precisión por clase@10",
                "precision_categoria",
                _bar_groups(dataset),
                (RANDOM, EXACT_STYLE, IVF, HNSW),
            )
            for dataset in DATASETS
        ),
    ),
    Figure(
        filename="multimedia-caracteristicas",
        report="multimedia",
        title="De archivos a vectores: lo que cuesta cada etapa",
        subtitle=(
            "Extracción con 12 procesos; vocabulario de 256 palabras con k-means\n"
            "sobre una muestra de 2 000 objetos; CREATE INDEX hace todo, una vez.\n"
            f"{READING_GUIDE}"
        ),
        rows_label=OBJECTS,
        columns=2,
        legend_columns=2,
        panels=tuple(_stages_panel(dataset) for dataset in DATASETS),
    ),
    Figure(
        filename="multimedia-indice",
        report="multimedia",
        title="Índices de vectores: construcción y memoria (coseno)",
        subtitle=(
            "Construir empieza con los vectores TF-IDF ya calculados; un hilo en todas las "
            "bibliotecas. Mediana de 3 construcciones.\n"
            "La memoria del HNSW del gestor es la de su estructura de FAISS; el espacio en "
            "disco de cada índice está en la tabla del README.\n"
            "Ejes logarítmicos. El valor escrito es el de 100 000 objetos."
        ),
        rows_label=OBJECTS,
        columns=3,
        legend_columns=4,
        panels=(
            compare(
                metric_operation(IMAGES, VectorMetric.COSINE, BUILD),
                "Construcción · imágenes",
                TOTAL_TIME,
                WITH_EXACT,
            ),
            compare(
                metric_operation(AUDIO, VectorMetric.COSINE, BUILD),
                "Construcción · audio",
                TOTAL_TIME,
                WITH_EXACT,
            ),
            _memory_panel(IMAGES),
        ),
    ),
    Figure(
        filename="multimedia-capas",
        report="multimedia",
        title="Cuánto cuesta cada capa del gestor (coseno)",
        subtitle=(
            "La misma búsqueda en FAISS directo, en el índice del gestor y por SQL;\n"
            "por SQL, además, se extrae el archivo de la consulta.\n"
            f"{READING_GUIDE}"
        ),
        rows_label=OBJECTS,
        columns=2,
        legend_columns=2,
        panels=tuple(
            _layers_panel(dataset, method) for dataset in DATASETS for method in LAYER_TECHNIQUES
        ),
    ),
    _gallery(IMAGES, "multimedia-galeria-imagenes", True, "fotos"),
    _gallery(AUDIO, "multimedia-galeria-audio", False, "clips"),
)
