"""Árbol del plan de ejecución, tal como lo verá el panel del frontend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TREE_BRANCH = "├─ "
TREE_LAST = "└─ "
TREE_PIPE = "│  "
TREE_BLANK = "   "


@dataclass(frozen=True, slots=True)
class PlanNode:
    """Un paso del plan: qué operación se hizo y sobre qué.

    Attributes:
        operation: nombre del operador, p. ej. `IndexLookup`.
        detail: qué estructura usó y con qué argumentos.
        children: operadores de los que consume filas.
    """

    operation: str
    detail: str = ""
    children: tuple[PlanNode, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "detail": self.detail,
            "children": [child.to_dict() for child in self.children],
        }

    def render(self) -> str:
        """Dibuja el plan como un árbol de texto."""
        return "\n".join(self._lines(prefix="", is_last=True, is_root=True))

    def _lines(self, prefix: str, is_last: bool, is_root: bool) -> list[str]:
        connector = "" if is_root else (TREE_LAST if is_last else TREE_BRANCH)
        label = self.operation if not self.detail else f"{self.operation}: {self.detail}"
        lines = [f"{prefix}{connector}{label}"]
        child_prefix = prefix if is_root else prefix + (TREE_BLANK if is_last else TREE_PIPE)
        for position, child in enumerate(self.children):
            lines.extend(
                child._lines(child_prefix, position == len(self.children) - 1, is_root=False)
            )
        return lines
