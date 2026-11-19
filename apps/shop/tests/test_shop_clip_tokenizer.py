"""El tokenizador de CLIP (librería `tokenizers`) contra el `CLIPTokenizer` de Python de
Hugging Face (transformers 4.57.1), que implementa el BPE de OpenAI por su cuenta."""

from __future__ import annotations

import pytest

from shop.clip import CONTEXT_LENGTH, ClipTokenizer

START_OF_TEXT = 49406
END_OF_TEXT = 49407
REFERENCE_IDS = {
    "a photo of a cat": [49406, 320, 1125, 539, 320, 2368, 49407],
    "Fender Stratocaster 12-string electric guitar, Sunburst!": [
        49406, 17117, 28878, 8449, 272, 273, 268, 9696, 5031, 5084, 267, 968, 13005, 256, 49407,
    ],
    "  Drum   sticks\n(5A) — maple  ": [49406, 8698, 10016, 263, 276, 320, 264, 2005, 10570, 49407],
    "Ibanez's TS9 Tube-Screamer pedal": [
        49406, 47145, 8387, 568, 590, 280, 3308, 268, 1795, 16147, 19621, 49407,
    ],
}  # fmt: skip


@pytest.mark.parametrize("text", list(REFERENCE_IDS))
def test_ids_match_the_reference_tokenizer(clip_tokenizer: ClipTokenizer, text: str):
    (ids,) = clip_tokenizer.encode([text])
    assert ids.tolist() == REFERENCE_IDS[text]


def test_a_long_text_is_cut_to_the_context_keeping_its_end(clip_tokenizer: ClipTokenizer):
    (ids,) = clip_tokenizer.encode([" ".join(["guitar"] * 100)])
    assert len(ids) == CONTEXT_LENGTH
    assert (ids[0], ids[-1]) == (START_OF_TEXT, END_OF_TEXT)


def test_a_batch_is_padded_with_end_of_text(clip_tokenizer: ClipTokenizer):
    short, long = clip_tokenizer.encode(["a photo of a cat", "a photo of a black cat"])
    assert len(short) == len(long)
    assert short.tolist()[-2:] == [END_OF_TEXT, END_OF_TEXT]


def test_an_empty_text_is_start_and_end(clip_tokenizer: ClipTokenizer):
    (ids,) = clip_tokenizer.encode([""])
    assert ids.tolist() == [START_OF_TEXT, END_OF_TEXT]
