# Documentación

| Archivo | Qué es |
|---|---|
| [`informe.pdf`](informe.pdf) | Informe de las Partes 1 a 5: diseño arquitectónico, dominio de datos, explicación de los algoritmos y parte experimental |
| [`latex/`](latex/) | Fuente LaTeX del informe |

La documentación técnica de cada módulo está junto a su código: empieza por el
[README del repositorio](../README.md).

## Compilar el informe

```bash
brew install tectonic                # una sola vez
uv run --no-sync --with matplotlib python -m benchmarks.plot  # si cambiaron los resultados
make -C docs/latex informe           # genera docs/informe.pdf
```

Las gráficas se toman de [`benchmarks/figures/`](../benchmarks/figures/). Los datos del
alumno, el enlace al repositorio y el número de pruebas están al principio de
[`latex/informe.tex`](latex/informe.tex).
