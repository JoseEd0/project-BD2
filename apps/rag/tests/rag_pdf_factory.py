"""PDF diminutos escritos a mano para los tests: cada página es una lista de líneas en
Helvetica, sin depender de ninguna librería que genere PDF."""

from __future__ import annotations

from collections.abc import Sequence

FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
PAGE_TEMPLATE = (
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
)


def pdf_bytes(pages: Sequence[Sequence[str]]) -> bytes:
    """Un PDF válido con una página por elemento de `pages`; una lista vacía es una página
    sin texto, como la de un documento escaneado."""
    objects: list[bytes] = [CATALOG, b"", FONT]
    kids = []
    for lines in pages:
        objects.append(_stream(_text_operators(lines)))
        objects.append(PAGE_TEMPLATE % len(objects))
        kids.append(len(objects))
    references = b" ".join(b"%d 0 R" % kid for kid in kids)
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (references, len(kids))
    return _serialize(objects)


def _text_operators(lines: Sequence[str]) -> bytes:
    if not lines:
        return b""
    shown = " ".join(f"({_escape(line)}) Tj T*" for line in lines)
    return f"BT /F1 11 Tf 14 TL 72 760 Td {shown} ET".encode("latin-1")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _stream(content: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)


def _serialize(objects: Sequence[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    cross_reference = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    output += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        cross_reference,
    )
    return bytes(output)
