# Minigestor de Base de Datos Multimodal

Gestor de base de datos construido desde cero: almacenamiento en disco, índices, procesamiento de
consultas SQL, búsqueda espacial, full-text y multimedia. La especificación funcional completa está
en [`Proyecto_Integrador_BD2.md`](./Proyecto_Integrador_BD2.md).

## Estado

Implementado y probado: el front-end SQL (lexer, AST y parser) con soporte para el núcleo
relacional y las extensiones espacial, textual y multimedia. El resto de módulos está en curso.

## Organización

```
src/sql/             front-end SQL: tokens, lexer, AST y parser
tests/               tests unitarios (espejo de src/)
docs/                referencias técnicas
```

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Uso

```python
from sql import parse

statement = parse("SELECT * FROM tiendas WHERE distancia(ubicacion, POINT(-12.04, -77.04)) < 5000")
```

La gramática completa está documentada en [`docs/sql-grammar.md`](./docs/sql-grammar.md).

## Verificación

```bash
.venv/bin/ruff check
.venv/bin/mypy
.venv/bin/python -m pytest -q
```

## Autor

**José Huamaní** — Ciencias de la Computación, UTEC
