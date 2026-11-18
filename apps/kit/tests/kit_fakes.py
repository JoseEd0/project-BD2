"""Un gestor de mentira para los tests del kit: responde como el API real (la forma de sus
respuestas y errores) y anota lo que recibe. Los tests del cliente contra el gestor de
verdad están en `tests/client/`, y el servicio de ejemplo contra él, en `tests/kit/`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from minigestor_client import REQUEST_ID_HEADER

AUTHORIZATION_HEADER = "Authorization"
ENGINE_URL = "http://gestor.test:8000"


@dataclass
class FakeEngine:
    """Responde `/ready` y `/query`. `rows` son las filas que devuelve un `SELECT` por su
    primer parámetro; `failures` fuerza la respuesta de error de un SQL por su prefijo."""

    ready: bool = True
    reachable: bool = True
    rows: dict[object, list[object]] = field(default_factory=dict)
    failures: dict[str, tuple[int, str]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.reachable:
            raise httpx.ConnectError("conexión rechazada", request=request)
        echo = {REQUEST_ID_HEADER: request.headers[REQUEST_ID_HEADER]}
        if request.url.path == "/ready":
            status = 200 if self.ready else 503
            return httpx.Response(status, json={"status": "x"}, headers=echo)
        body = json.loads(request.content)
        return self._answer(body["sql"], body["params"], echo)

    def queries(self) -> list[tuple[str, str | None]]:
        """El SQL de cada `/query` recibido y la clave con que llegó."""
        return [
            (json.loads(request.content)["sql"], request.headers.get(AUTHORIZATION_HEADER))
            for request in self.requests
            if request.url.path == "/query"
        ]

    def _answer(self, sql: str, params: list[object], echo: dict[str, str]) -> httpx.Response:
        for prefix, (status, kind) in self.failures.items():
            if sql.startswith(prefix):
                detail = {"error": f"falló {kind}", "kind": kind, "line": None, "column": None}
                return httpx.Response(status, json={"detail": detail}, headers=echo)
        found = self.rows.get(params[0]) if params else None
        payload = {
            "columns": ["id", "texto"],
            "rows": [found] if found else [],
            "total_rows": 1 if found else 0,
            "affected_rows": 1,
        }
        return httpx.Response(
            200, json=payload, headers={**echo, "Server-Timing": "exec;dur=1.5, total;dur=2.0"}
        )
