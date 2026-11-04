"""Las listas de postings en memoria."""

from array import array

from index.inverted.format import POSTING_TYPECODE
from index.inverted.postings import EMPTY_POSTING_LIST, PostingList


def posting_list(doc_ids: list[int], frequencies: list[int]) -> PostingList:
    return PostingList(array(POSTING_TYPECODE, doc_ids), array(POSTING_TYPECODE, frequencies))


def test_a_restricted_list_keeps_only_those_documents_in_order():
    postings = posting_list([2, 5, 9, 14, 30], [1, 3, 1, 2, 7])
    restricted = postings.restricted_to({30, 5, 14, 99})
    assert list(restricted.doc_ids) == [5, 14, 30]
    assert list(restricted.term_frequencies) == [3, 2, 7]


def test_restricting_to_nothing_or_to_an_empty_list_gives_an_empty_list():
    assert len(posting_list([1, 2], [1, 1]).restricted_to(set())) == 0
    assert len(EMPTY_POSTING_LIST.restricted_to({1, 2})) == 0


def test_restricting_to_every_document_changes_nothing():
    postings = posting_list([3, 4], [2, 1])
    assert postings.restricted_to({3, 4}) == postings
