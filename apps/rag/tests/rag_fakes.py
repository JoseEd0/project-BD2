"""Respuestas de mentira del OAI-PMH de arXiv y un cliente que las sirve sin red."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable

from rag.fetch import PoliteClient
from rag.settings import HttpSettings

CC_BY = "http://creativecommons.org/licenses/by/4.0/"
ARXIV_DEFAULT = "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"

DELETED_RECORD = """
    <record><header status="deleted"><identifier>oai:arXiv.org:2001.00001</identifier>
      <datestamp>2025-01-01</datestamp></header></record>"""


def record_xml(
    arxiv_id: str,
    categories: str = "cs.DB",
    license_url: str | None = CC_BY,
    created: str = "2025-03-01",
) -> str:
    license_element = f"<license>{license_url}</license>" if license_url else ""
    return f"""
    <record>
      <header><identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>2025-03-02</datestamp><setSpec>cs:cs:DB</setSpec></header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{arxiv_id}</id><created>{created}</created>
          <authors>
            <author><keyname>Codd</keyname><forenames>Edgar F.</forenames></author>
            <author><keyname>Gray</keyname><forenames>Jim</forenames><suffix>Jr.</suffix></author>
          </authors>
          <title>A relational
            model   of data</title>
          <categories>{categories}</categories>
          {license_element}
          <abstract>  We present
            a model.  </abstract>
        </arXiv>
      </metadata>
    </record>"""


def page_xml(records: str, token: str = "") -> bytes:
    resumption = f"<resumptionToken cursor='0'>{token}</resumptionToken>" if token else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <responseDate>2026-11-18T00:00:00Z</responseDate>
      <ListRecords>{records}{resumption}</ListRecords>
    </OAI-PMH>""".encode()


def oai_error(code: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <error code="{code}">mensaje</error></OAI-PMH>""".encode()


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *details: object) -> None:
        return None


def client_serving(
    responses: Callable[[str], bytes], settings: HttpSettings | None = None
) -> PoliteClient:
    """Un cliente sin pausas cuya respuesta a cada URL la decide `responses` (que puede
    lanzar `urllib.error.HTTPError` o `URLError`)."""

    def open_url(request: urllib.request.Request, timeout: float) -> FakeResponse:
        return FakeResponse(responses(request.full_url))

    return PoliteClient(
        "test", 0.0, settings or HttpSettings(), sleep=lambda seconds: None, opener=open_url
    )
