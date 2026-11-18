"""Lo que el cliente usa del gestor sigue en su contrato versionado (`openapi/engine.json`).

La CI vuelve a exportar el contrato desde el código del gestor y falla si cambió sin
versionarse; estos tests fallan si el contrato nuevo ya no tiene una ruta o un campo que el
cliente lee. Así un cambio del API rompe la CI del cliente y no una app en producción.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minigestor_client.calls import (
    ENGINE_ROUTES,
    MEDIA_FILE_FIELD,
    MEDIA_FILE_PATH,
    MEDIA_PATH_PARAMETER,
    MEDIA_QUERIES_PATH,
    MEDIA_SEGMENTS_PATH,
    MEDIA_THUMBNAIL_PATH,
    QUERY_PATH,
    ROWS_PATH,
    TABLES_PATH,
    QueryBody,
    SegmentsForm,
)
from minigestor_client.models import (
    ENGINE_MODELS,
    TRACE_FIELDS,
    EngineModel,
    LoadReport,
    MediaUpload,
    QueryResult,
    SegmentSearch,
    TableInfo,
)

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "openapi" / "engine.json"
REFERENCE_PREFIX = "#/components/schemas/"
SUCCESS = "200"


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def schema(contract: dict[str, Any], name: str) -> dict[str, Any]:
    return contract["components"]["schemas"][name]


def referenced(contract: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    return schema(contract, reference["$ref"].removeprefix(REFERENCE_PREFIX))


def operation(contract: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    return contract["paths"][path][method.lower()]


def success_schema(contract: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    content = operation(contract, method, path)["responses"][SUCCESS]["content"]
    return content["application/json"]["schema"]


def body_schema(
    contract: dict[str, Any], method: str, path: str, media_type: str
) -> dict[str, Any]:
    content = operation(contract, method, path)["requestBody"]["content"][media_type]
    return referenced(contract, content["schema"])


@pytest.mark.parametrize(("method", "path"), sorted(ENGINE_ROUTES))
def test_every_route_the_client_calls_is_in_the_contract(contract, method: str, path: str):
    assert method.lower() in contract["paths"].get(path, {}), f"{method} {path}"


@pytest.mark.parametrize("model", ENGINE_MODELS, ids=lambda model: model.__name__)
def test_every_field_the_client_reads_is_in_the_contract(contract, model: type[EngineModel]):
    engine = schema(contract, model.ENGINE_SCHEMA)
    reads = set(model.model_fields) - TRACE_FIELDS
    assert reads <= set(engine["properties"])


@pytest.mark.parametrize("model", ENGINE_MODELS, ids=lambda model: model.__name__)
def test_a_field_the_client_needs_is_always_sent(contract, model: type[EngineModel]):
    engine = schema(contract, model.ENGINE_SCHEMA)
    needed = {name for name, field in model.model_fields.items() if field.is_required()}
    assert needed <= set(engine.get("required", []))


@pytest.mark.parametrize(
    ("method", "path", "model"),
    [
        ("POST", QUERY_PATH, QueryResult),
        ("POST", ROWS_PATH, LoadReport),
        ("POST", MEDIA_QUERIES_PATH, MediaUpload),
        ("POST", MEDIA_SEGMENTS_PATH, SegmentSearch),
    ],
)
def test_each_route_answers_the_schema_its_model_reads(contract, method, path, model):
    assert referenced(contract, success_schema(contract, method, path)) == schema(
        contract, model.ENGINE_SCHEMA
    )


def test_the_table_listing_is_a_list_of_tables(contract):
    listing = success_schema(contract, "GET", TABLES_PATH)
    assert listing["type"] == "array"
    assert referenced(contract, listing["items"]) == schema(contract, TableInfo.ENGINE_SCHEMA)


def test_the_query_body_has_every_field_the_client_sends(contract):
    sent = set(QueryBody.__annotations__)
    assert sent <= set(body_schema(contract, "POST", QUERY_PATH, "application/json")["properties"])


def test_the_segments_form_has_every_field_the_client_sends(contract):
    sent = set(SegmentsForm.__annotations__) | {MEDIA_FILE_FIELD}
    form = body_schema(contract, "POST", MEDIA_SEGMENTS_PATH, "multipart/form-data")
    assert sent <= set(form["properties"])


def test_a_query_file_is_uploaded_in_the_field_the_client_uses(contract):
    form = body_schema(contract, "POST", MEDIA_QUERIES_PATH, "multipart/form-data")
    assert MEDIA_FILE_FIELD in form["properties"]


@pytest.mark.parametrize("path", [MEDIA_FILE_PATH, MEDIA_THUMBNAIL_PATH])
def test_files_are_named_by_the_parameter_the_client_sends(contract, path: str):
    parameters = operation(contract, "GET", path)["parameters"]
    assert {(item["name"], item["in"]) for item in parameters} >= {(MEDIA_PATH_PARAMETER, "query")}
