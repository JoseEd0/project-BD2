"""Los enlaces relativos de los Markdown versionados llevan a un archivo y a un ancla que existen."""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import cache
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CODE_FENCE = "```"
MARKDOWN_FILES = "*.md"
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$")
INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:")
# GitHub deja letras, cifras, guiones y espacios del título, y cambia cada espacio por un guion.
NON_SLUG_CHARACTER = re.compile(r"[^\w\- ]")


def tracked_markdown_files() -> list[Path]:
    if shutil.which("git") is None:
        pytest.skip("sin git no se sabe qué Markdown están versionados")
    listing = subprocess.run(
        ["git", "ls-files", MARKDOWN_FILES],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPOSITORY_ROOT / name for name in listing.stdout.split()]


def lines_outside_code(path: Path) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    inside_code = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith(CODE_FENCE):
            inside_code = not inside_code
        elif not inside_code:
            lines.append((number, line))
    return lines


def heading_slug(title: str) -> str:
    text = INLINE_LINK.sub(r"\1", title).replace("`", "").strip().lower()
    return NON_SLUG_CHARACTER.sub("", text).replace(" ", "-")


@cache
def anchors(path: Path) -> frozenset[str]:
    occurrences: dict[str, int] = {}
    found: set[str] = set()
    for _, line in lines_outside_code(path):
        match = HEADING.match(line)
        if match is None:
            continue
        slug = heading_slug(match.group(1))
        repeated = occurrences.get(slug, 0)
        found.add(slug if repeated == 0 else f"{slug}-{repeated}")
        occurrences[slug] = repeated + 1
    return frozenset(found)


def broken_links(path: Path) -> list[str]:
    problems: list[str] = []
    for number, line in lines_outside_code(path):
        for match in LINK.finditer(line):
            target = match.group(1)
            if URL_SCHEME.match(target):
                continue
            file_part, _, anchor = target.partition("#")
            destination = (path.parent / file_part).resolve() if file_part else path
            where = f"{path.relative_to(REPOSITORY_ROOT)}:{number}: {target}"
            if not destination.exists():
                problems.append(f"{where} (no existe)")
            elif anchor and destination.suffix == ".md" and anchor not in anchors(destination):
                problems.append(f"{where} (sin esa sección)")
    return problems


def test_every_relative_markdown_link_resolves() -> None:
    problems = [problem for path in tracked_markdown_files() for problem in broken_links(path)]

    assert problems == []


def test_code_fences_are_balanced() -> None:
    unbalanced = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in tracked_markdown_files()
        if sum(
            line.lstrip().startswith(CODE_FENCE)
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        % 2
    ]

    assert unbalanced == []
