"""Un modelo de lenguaje simulado, sin red ni costo, para los tests y la CI.

No entiende nada: cita los documentos que comparten alguna palabra de contenido con la
pregunta y, si ninguno la comparte, dice que los documentos no la responden. Cuenta cada
palabra como un token de salida y corta en `max_tokens`, como la API. Basta para
probar de punta a punta el streaming, las citas, el «no está en los documentos», la charla
sin documentos y el formato SSE sin una clave de la API. Se elige con `RAG_LLM_PROVIDER=simulated`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from html import unescape

from .llm import Completion, ModelEvent, StopReason, TextDelta
from .pricing import SIMULATED_MODEL, Attempt, TokenUsage
from .prompt import Prompt

NOT_IN_DOCUMENTS = "Los documentos recuperados no responden esta pregunta."
SMALL_TALK_ANSWER = (
    "Hola. Respondo preguntas sobre los papers del corpus con la página de cada afirmación."
)
CITED_ANSWER = "Respuesta simulada, sin modelo de lenguaje: el documento {n} trata «{word}» [{n}]."
# El texto de los documentos y de la pregunta llega escapado: no tiene `<`.
DOCUMENT = re.compile(
    r'<document index="(\d+)">\n<source>[^<]*</source>\n<document_content>\n([^<]*)\n'
)
QUESTION = re.compile(r"<question>([^<]*)</question>")
WORD = re.compile(r"\w+")
# Palabras de cuatro letras o más que aparecen en cualquier pregunta y no dicen su tema.
FUNCTION_WORDS = frozenset(
    {
        "what", "which", "when", "where", "does", "with", "from", "that", "this", "there",
        "about", "como", "cómo", "cuál", "cuáles", "para", "porque", "sobre", "entre", "esta",
        "este", "estos", "estas", "donde", "dónde", "qué", "cuando", "cuándo", "hace", "hacen",
    }
)  # fmt: skip
MIN_CONTENT_WORD_LENGTH = 4
MAX_CITED_DOCUMENTS = 3


class SimulatedModel:
    """Un `AnswerModel` determinista; `name` es `simulated` y su precio, cero."""

    @property
    def name(self) -> str:
        return SIMULATED_MODEL

    async def stream(self, prompt: Prompt, max_tokens: int) -> AsyncIterator[ModelEvent]:
        words = _answer(prompt).split(" ")
        # Un token por palabra: con un tope menor que la respuesta, se corta como la API.
        emitted = words[:max_tokens]
        for position, word in enumerate(emitted):
            yield TextDelta(word if position == 0 else " " + word)
        yield Completion(
            model=SIMULATED_MODEL,
            stop_reason=StopReason.END_TURN if emitted == words else StopReason.MAX_TOKENS,
            refusal_category=None,
            attempts=(Attempt(SIMULATED_MODEL, TokenUsage()),),
        )


def _answer(prompt: Prompt) -> str:
    if prompt.conversational:
        return SMALL_TALK_ANSWER
    question = QUESTION.search(prompt.question)
    words = _content_words(unescape(question.group(1)) if question else "")
    matches = [
        (int(index), shared[0])
        for index, text in DOCUMENT.findall(prompt.documents)
        if (shared := _shared(words, _content_words(unescape(text))))
    ]
    if not matches:
        return NOT_IN_DOCUMENTS
    return " ".join(
        CITED_ANSWER.format(n=n, word=word) for n, word in matches[:MAX_CITED_DOCUMENTS]
    )


def _content_words(text: str) -> list[str]:
    return [
        word
        for word in WORD.findall(text.casefold())
        if len(word) >= MIN_CONTENT_WORD_LENGTH and word not in FUNCTION_WORDS
    ]


def _shared(question_words: Sequence[str], document_words: Sequence[str]) -> list[str]:
    present = set(document_words)
    return [word for word in question_words if word in present]
