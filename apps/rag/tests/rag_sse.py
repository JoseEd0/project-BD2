"""Leer un cuerpo `text/event-stream` en los tests: una lista de `(evento, data)`."""

from __future__ import annotations

import json
from typing import Any

SseEvent = tuple[str, dict[str, Any]]


def parse_sse(body: str) -> list[SseEvent]:
    """Los eventos de `body`; falla si alguno no tiene exactamente `event:` y un `data:`."""
    events: list[SseEvent] = []
    for frame in body.split("\n\n"):
        if not frame:
            continue
        name_line, data_line = frame.split("\n")
        assert name_line.startswith("event: ") and data_line.startswith("data: "), frame
        events.append(
            (name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: ")))
        )
    assert body.endswith("\n\n")
    return events


def names(events: list[SseEvent]) -> list[str]:
    return [name for name, _ in events]


def data_of(events: list[SseEvent], name: str) -> dict[str, Any]:
    (data,) = [data for event, data in events if event == name]
    return data
