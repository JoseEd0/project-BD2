"""Fixtures del dataset de la tienda."""

from __future__ import annotations

import pytest
from apps.shop.tests.shop_support import FakeTransport


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()
