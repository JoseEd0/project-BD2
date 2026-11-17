# Servicios de la Parte 5

Cada aplicación del Anexo A es un servicio aparte que usa el gestor como lo haría con
cualquier base de datos: **solo por su API REST**. El gestor es un contenedor con estado en
una red interna; cada servicio es otro contenedor, sin estado, que escala con réplicas.

```
apps/
  client/      minigestor_client: cliente tipado del API del gestor
  kit/         appkit: configuración, logs, salud y errores comunes a los servicios
```

## Reglas

- **Ningún servicio importa el código del gestor** (`src/`). Lo comprueba
  [`tests/test_service_boundaries.py`](../tests/test_service_boundaries.py) sobre cada
  archivo de `apps/`.
- Cada servicio es un miembro del espacio de trabajo de uv, con su `pyproject.toml`,
  `src/<paquete>/` y `tests/`. Se registra en `[tool.uv.workspace]` del `pyproject.toml`
  raíz, y sus versiones quedan fijadas en el `uv.lock` común.
- Los tests de un servicio se llaman `test_<servicio>_*.py`: pytest importa cada archivo por
  su nombre, así que dos servicios no pueden tener un `test_api.py` cada uno.
- `ruff` y `mypy --strict` cubren `apps/*/src` igual que el gestor.

## Entorno

```bash
uv sync --all-packages          # gestor, servicios y herramientas de desarrollo
uv run pytest apps -q           # solo los tests de los servicios
```
