from __future__ import annotations

import urllib.parse

import pytest
from rag_fakes import (
    ARXIV_DEFAULT,
    CC_BY,
    DELETED_RECORD,
    client_serving,
    oai_error,
    page_xml,
    record_xml,
)

from rag.arxiv import (
    ArxivRecord,
    harvest,
    in_scope,
    list_records_url,
    oai_set,
    parse_records_page,
    resumption_url,
    select_papers,
)
from rag.errors import HarvestError
from rag.settings import HarvestSettings


def make_record(
    arxiv_id: str, category: str = "cs.DB", license_url: str | None = CC_BY, created: str = ""
) -> ArxivRecord:
    return ArxivRecord(
        arxiv_id=arxiv_id,
        title="t",
        authors=(),
        created=created or "2025-01-01",
        abstract="",
        categories=(category, "cs.AI"),
        license=license_url,
    )


def test_a_list_records_page_is_parsed_with_license_and_authors() -> None:
    page = parse_records_page(
        page_xml(record_xml("2503.00001", "cs.IR cs.DB") + DELETED_RECORD, "abc")
    )
    assert page.resumption_token == "abc"
    [record] = page.records
    assert record.arxiv_id == "2503.00001"
    assert record.title == "A relational model of data"
    assert record.authors == ("Edgar F. Codd", "Jim Gray Jr.")
    assert record.abstract == "We present a model."
    assert record.primary_category == "cs.IR"
    assert record.categories == ("cs.IR", "cs.DB")
    assert record.license == CC_BY
    assert record.year == 2025


def test_a_record_without_license_and_an_old_style_identifier() -> None:
    [record] = parse_records_page(page_xml(record_xml("cs/0101001", license_url=None))).records
    assert record.license is None
    assert record.file_stem == "cs_0101001"


def test_the_last_page_has_no_token_and_no_records_match_is_empty() -> None:
    assert parse_records_page(page_xml(record_xml("1"))).resumption_token is None
    empty = parse_records_page(oai_error("noRecordsMatch"))
    assert empty.records == ()
    assert empty.resumption_token is None


@pytest.mark.parametrize(
    "document", [oai_error("badResumptionToken"), b"<html>no</html", b"<OAI-PMH/>"]
)
def test_errors_and_garbage_from_the_server_are_reported(document: bytes) -> None:
    with pytest.raises(HarvestError):
        parse_records_page(document)


def test_records_round_trip_through_json() -> None:
    [record] = parse_records_page(page_xml(record_xml("2503.00001"))).records
    assert ArxivRecord.from_json(record.to_json()) == record


def test_categories_map_to_oai_sets() -> None:
    assert oai_set("cs.DB") == "cs:cs:DB"
    with pytest.raises(HarvestError):
        oai_set("cs")


def test_the_resumption_token_travels_encoded() -> None:
    settings = HarvestSettings(endpoint="https://oai.test/oai")
    first = urllib.parse.parse_qs(urllib.parse.urlsplit(list_records_url(settings, "cs.IR")).query)
    assert first == {
        "verb": ["ListRecords"],
        "metadataPrefix": ["arXiv"],
        "set": ["cs:cs:IR"],
        "from": [settings.from_date],
    }
    token = "verb%3DListRecords%26skip%3D6"
    query = urllib.parse.urlsplit(resumption_url(settings, token)).query
    assert urllib.parse.parse_qs(query)["resumptionToken"] == [token]


def test_harvest_follows_tokens_and_keeps_a_cross_listed_paper_once() -> None:
    settings = HarvestSettings(endpoint="https://oai.test/oai", categories=("cs.DB", "cs.IR"))
    requested: list[str] = []

    def respond(url: str) -> bytes:
        requested.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("resumptionToken") == ["next"]:
            return page_xml(record_xml("2"))
        if query.get("set") == ["cs:cs:DB"]:
            return page_xml(record_xml("1"), "next")
        return page_xml(record_xml("2", "cs.IR") + record_xml("3", "cs.IR"))

    records = harvest(client_serving(respond), settings)
    assert [record.arxiv_id for record in records] == ["1", "2", "3"]
    assert len(requested) == 3


def test_only_papers_whose_primary_category_is_wanted_are_in_scope() -> None:
    records = [make_record("1", "cs.DB"), make_record("2", "cs.LG"), make_record("3", "cs.IR")]
    assert [record.arxiv_id for record in in_scope(records, ["cs.DB", "cs.IR"])] == ["1", "3"]


def test_selection_keeps_licensed_papers_newest_first_alternating_categories() -> None:
    records = [
        make_record("db-old", "cs.DB", created="2024-01-01"),
        make_record("db-new", "cs.DB", created="2025-06-01"),
        make_record("db-closed", "cs.DB", ARXIV_DEFAULT, created="2026-01-01"),
        make_record("ir-1", "cs.IR", created="2025-01-01"),
        make_record("lg", "cs.LG", created="2026-01-01"),
    ]
    selected = select_papers(records, ["cs.DB", "cs.IR"], limit=10)
    assert [record.arxiv_id for record in selected] == ["db-new", "ir-1", "db-old"]
    assert [r.arxiv_id for r in select_papers(records, ["cs.DB", "cs.IR"], 2)] == ["db-new", "ir-1"]
    assert select_papers(records, ["cs.DB"], 0) == []
