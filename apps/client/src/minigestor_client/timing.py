"""Lectura de la cabecera `Server-Timing` que devuelve el gestor."""

from __future__ import annotations

SERVER_TIMING_HEADER = "Server-Timing"
METRIC_SEPARATOR = ","
PARAMETER_SEPARATOR = ";"
DURATION_PARAMETER = "dur="


def parse_server_timing(header: str | None) -> dict[str, float]:
    """Los milisegundos de cada métrica, en el orden de la cabecera.

    Una métrica sin `dur` o con un valor que no es un número se descarta: la cabecera es
    informativa y una entrada mal formada no debe tumbar la respuesta.
    """
    metrics: dict[str, float] = {}
    for entry in (header or "").split(METRIC_SEPARATOR):
        name, *parameters = (part.strip() for part in entry.split(PARAMETER_SEPARATOR))
        duration = _duration_of(parameters)
        if name and duration is not None:
            metrics[name] = duration
    return metrics


def _duration_of(parameters: list[str]) -> float | None:
    for parameter in parameters:
        if parameter.lower().startswith(DURATION_PARAMETER):
            try:
                return float(parameter[len(DURATION_PARAMETER) :])
            except ValueError:
                return None
    return None
