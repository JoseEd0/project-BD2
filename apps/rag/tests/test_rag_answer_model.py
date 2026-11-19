"""Qué modelo de lenguaje abre el servicio según su configuración."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from pydantic import ValidationError

from rag.claude import ClaudeModel
from rag.config import LlmProvider, RagSettings, open_answer_model
from rag.llm import AnswerModel
from rag.pricing import SONNET_5_5
from rag.simulated import SimulatedModel


def settings(**values: object) -> RagSettings:
    return RagSettings(
        engine_url="http://engine:8000",
        engine_key="clave",
        models_dir=Path("modelos"),
        **values,  # type: ignore[arg-type]
    )


def opened(configured: RagSettings) -> AnswerModel | None:
    async def run() -> AnswerModel | None:
        async with open_answer_model(configured) as model:
            return model

    return anyio.run(run)


def test_the_simulated_provider_needs_no_key():
    assert isinstance(opened(settings(llm_provider=LlmProvider.SIMULATED)), SimulatedModel)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_anthropic_without_a_key_opens_no_model(key: str | None):
    assert opened(settings(anthropic_api_key=key)) is None


def test_anthropic_with_a_key_opens_claude_with_the_configured_model():
    model = opened(settings(anthropic_api_key="sk-de-prueba"))
    assert isinstance(model, ClaudeModel)
    assert model.name == SONNET_5_5


def test_a_model_without_a_price_is_refused():
    with pytest.raises(ValidationError, match="precios"):
        settings(answer_model="claude-desconocido")


def test_the_answer_settings_come_from_the_service_settings():
    answering = settings(answer_chunks=5, answer_max_tokens=900, rrf_k=10).answering()
    assert (answering.chunks, answering.max_tokens, answering.rrf_k) == (5, 900, 10)
