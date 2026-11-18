"""`Server-Timing` como milisegundos por etapa."""

from __future__ import annotations

from minigestor_client import parse_server_timing


def test_each_metric_keeps_its_duration_in_order():
    header = "parse;dur=0.412, plan;dur=0.130, exec;dur=3.201, total;dur=4.020"
    metrics = parse_server_timing(header)
    assert list(metrics) == ["parse", "plan", "exec", "total"]
    assert metrics["exec"] == 3.201


def test_descriptions_and_parameter_order_do_not_matter():
    assert parse_server_timing('db;desc="motor";dur=2.5') == {"db": 2.5}


def test_malformed_entries_are_dropped():
    assert parse_server_timing("sin-duracion, mala;dur=x, ;dur=1, bien;dur=1") == {"bien": 1.0}


def test_a_missing_header_is_empty():
    assert parse_server_timing(None) == {}
    assert parse_server_timing("") == {}
