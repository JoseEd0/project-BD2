"""Los turnos anteriores de una conversación y qué mensaje no es una pregunta al corpus.

El servicio no guarda conversaciones: la interfaz manda sus turnos anteriores con cada
pregunta y aquí se acotan (`bounded_history`). Un saludo, un agradecimiento o una pregunta
sobre la propia app (`is_small_talk`) no se busca en el corpus: el modelo responde sin
documentos, en pocas líneas, y sugiere qué preguntar. Una pregunta de seguimiento corta
(«¿y cuál es más rápido?») se busca junto con la anterior (`retrieval_query`), que es la
que nombra el tema.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .citations import CITATION

# Los mismos topes que una pregunta nueva y que la salida de una respuesta: un turno
# anterior más largo no viene de esta interfaz.
MAX_EXCHANGE_QUESTION_CHARACTERS = 1000
MAX_EXCHANGE_ANSWER_CHARACTERS = 12_000
MAX_HISTORY_EXCHANGES = 20

GREETINGS = frozenset(
    {
        "hola", "holi", "buenas", "buenos dias", "buenas tardes", "buenas noches", "saludos",
        "que tal", "hello", "hi", "hey", "good morning", "good afternoon", "gracias",
        "muchas gracias", "thanks", "thank you", "ok", "okay", "vale", "perfecto", "genial",
        "adios", "chau", "chao", "bye",
    }
)  # fmt: skip
META_QUESTIONS = frozenset(
    {
        "que puedes hacer", "que sabes hacer", "que haces", "quien eres", "que eres",
        "como funciona", "como funcionas", "como te uso", "ayuda", "ayudame", "help",
        "en que me puedes ayudar", "en que puedes ayudarme", "que puedo preguntar",
        "que puedo preguntarte", "que te puedo preguntar", "que papers tienes",
        "que documentos tienes", "de que sabes", "sobre que sabes", "what can you do",
        "who are you", "what are you", "how does this work", "what can i ask",
    }
)  # fmt: skip
NON_WORD = re.compile(r"[^\w\s]")
CITATION_WITH_SPACE = re.compile(r"[ \t]*" + CITATION.pattern)
SPACES = re.compile(r"\s+")

ExchangeQuestion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_EXCHANGE_QUESTION_CHARACTERS
    ),
]
ExchangeAnswer = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_EXCHANGE_ANSWER_CHARACTERS)
]


class Exchange(BaseModel):
    """Un turno anterior: la pregunta y la respuesta que recibió."""

    model_config = ConfigDict(frozen=True)

    question: ExchangeQuestion
    answer: ExchangeAnswer


def normalized(text: str) -> str:
    """`text` en minúsculas, sin tildes, sin signos y con un espacio entre palabras."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    return SPACES.sub(" ", NON_WORD.sub(" ", plain)).strip()


def is_small_talk(question: str) -> bool:
    """Si `question` es un saludo, un agradecimiento o una pregunta sobre la app, solos o
    juntos («hola, ¿qué puedes hacer?»), y no una pregunta sobre el corpus."""
    text = normalized(question)
    if text in GREETINGS or text in META_QUESTIONS:
        return True
    for greeting in GREETINGS:
        if text.startswith(greeting + " "):
            rest = text.removeprefix(greeting + " ")
            return rest in GREETINGS or rest in META_QUESTIONS
    return False


def bounded_history(history: Sequence[Exchange], turns: int, characters: int) -> list[Exchange]:
    """Los últimos `turns` turnos con respuesta, sin sus citas `[n]` (sus números eran de
    otros documentos) y con cada respuesta cortada en `characters`."""
    uncited = [
        Exchange(question=exchange.question, answer=_uncited(exchange.answer)[:characters])
        for exchange in history
    ]
    answered = [exchange for exchange in uncited if exchange.answer]
    return answered[-turns:] if turns else []


def retrieval_query(question: str, history: Sequence[Exchange], follow_up_words: int) -> str:
    """Lo que se busca en el corpus: `question`, o la pregunta anterior y `question` si esta
    tiene menos de `follow_up_words` palabras y hay un turno anterior."""
    if not history or len(question.split()) >= follow_up_words:
        return question
    return f"{history[-1].question} {question}"


def _uncited(answer: str) -> str:
    return CITATION_WITH_SPACE.sub("", answer).strip()
