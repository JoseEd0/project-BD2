# Compila el informe en docs/informe.pdf.
# Requiere tectonic (brew install tectonic); también funciona con pdflatex.
MOTOR   ?= tectonic
FUENTE  := informe.tex
SALIDA  := ..
PDF     := $(SALIDA)/informe.pdf

.PHONY: informe ver limpiar

# Toma las gráficas de benchmarks/figures/: regenerarlas antes con `python -m benchmarks.plot`.
informe:
	$(MOTOR) -X compile $(FUENTE) --outdir $(SALIDA)
	@echo "PDF en docs/informe.pdf"

ver: informe
	@open $(PDF)

limpiar:
	@rm -f $(PDF)
