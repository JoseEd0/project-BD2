from __future__ import annotations

import pytest

from rag.licenses import is_redistributable, license_family


@pytest.mark.parametrize(
    "url",
    [
        "http://creativecommons.org/licenses/by/4.0/",
        "https://creativecommons.org/licenses/by/3.0/",
        "http://creativecommons.org/licenses/by-sa/4.0/",
        "http://creativecommons.org/publicdomain/zero/1.0/",
        "http://creativecommons.org/licenses/publicdomain/",
    ],
)
def test_licenses_that_allow_redistribution_are_kept(url: str) -> None:
    assert is_redistributable(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
        "http://creativecommons.org/licenses/by-nc-sa/4.0/",
        "http://creativecommons.org/licenses/by-nc-nd/4.0/",
        "http://creativecommons.org/licenses/by-nd/4.0/",
        "http://creativecommons.org/licenses/by-nc/4.0/",
        "http://example.org/my-license",
        "",
        None,
    ],
)
def test_the_default_arxiv_license_and_restricted_ones_are_dropped(url: str | None) -> None:
    assert not is_redistributable(url)


def test_licenses_are_grouped_into_readable_families() -> None:
    assert license_family("http://creativecommons.org/licenses/by/4.0/") == "CC BY"
    assert license_family("http://creativecommons.org/licenses/by-sa/4.0/") == "CC BY-SA"
    assert license_family("HTTP://CREATIVECOMMONS.ORG/LICENSES/BY-NC-ND/4.0/") == "CC BY-NC-ND"
    assert (
        license_family("http://arxiv.org/licenses/nonexclusive-distrib/1.0/")
        == "arXiv no exclusiva"
    )
    assert license_family(None) == "sin licencia"
    assert license_family("http://example.org/") == "otra"
