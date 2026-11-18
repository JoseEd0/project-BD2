"""Las líneas JSON del servicio, con la forma de las del gestor."""

from __future__ import annotations

import json
import logging

import pytest

from appkit.logs import ACCESS_LOGGER, JsonFormatter, configure_logging, log_request
from minigestor_client import request_id_scope

ENGINE_LINE_FIELDS = {"time", "level", "logger", "message", "request_id"}
ACCESS_FIELDS = {"method", "path", "status", "service", "duration_ms"}


def formatted(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter().format(record))


def test_a_line_takes_the_request_id_of_the_request_in_course():
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "hola %s", ("mundo",), None)
    with request_id_scope("pedido-3"):
        line = formatted(record)
    assert set(line) == ENGINE_LINE_FIELDS
    assert (line["message"], line["request_id"], line["level"]) == (
        "hola mundo",
        "pedido-3",
        "INFO",
    )


def test_the_access_line_has_the_engine_fields(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG, logger=ACCESS_LOGGER)
    log_request("tienda", "GET", "/productos", 200, "pedido-4", 1.25)
    (record,) = caplog.records
    line = formatted(record)
    assert set(line) == ENGINE_LINE_FIELDS | ACCESS_FIELDS
    assert (line["service"], line["status"], line["request_id"]) == ("tienda", 200, "pedido-4")
    assert record.levelno == logging.INFO


@pytest.mark.parametrize(
    ("path", "status", "level"), [("/health", 200, logging.DEBUG), ("/ready", 503, logging.INFO)]
)
def test_probes_that_answer_well_stay_quiet(caplog, path: str, status: int, level: int):
    caplog.set_level(logging.DEBUG, logger=ACCESS_LOGGER)
    log_request("tienda", "GET", path, status, "sonda", 0.1)
    assert caplog.records[0].levelno == level


def test_an_exception_keeps_its_trace():
    try:
        raise ValueError("roto")
    except ValueError as error:
        record = logging.LogRecord(
            "app", logging.ERROR, __file__, 1, "falló", (), (ValueError, error, error.__traceback__)
        )
    assert "ValueError: roto" in str(formatted(record)["exception"])


def test_configure_logging_sends_everything_as_json(capsys: pytest.CaptureFixture[str]):
    root = logging.getLogger()
    uvicorn_access = logging.getLogger("uvicorn.access")
    saved = (root.handlers[:], root.level, uvicorn_access.disabled)
    try:
        configure_logging("warning")
        logging.getLogger("uvicorn.error").warning("arrancando")
        line = json.loads(capsys.readouterr().out.strip())
        assert uvicorn_access.disabled
    finally:
        root.handlers[:], level, uvicorn_access.disabled = saved
        root.setLevel(level)
    assert (line["logger"], line["message"]) == ("uvicorn.error", "arrancando")
