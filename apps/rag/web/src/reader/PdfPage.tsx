import { RenderingCancelledException, TextLayer, type RenderTask } from "pdfjs-dist";
import { useEffect, useRef, useState } from "react";

import { useElementWidth } from "./useElementWidth";
import { locatePassage, type PassageBounds } from "./passage";
import { openDocument } from "./pdfDocuments";

type PageState = "loading" | "ready" | "failed";

export interface PdfPageProps {
  readonly url: string;
  readonly page: number;
  /** El pasaje a marcar en esta página, o `null` si el fragmento no pasa por ella. */
  readonly passage: string | null;
  readonly bounds: PassageBounds;
  readonly negative: boolean;
  /** Si se encontró el pasaje en el texto de la página; `null` mientras carga o sin pasaje. */
  readonly onLocated: (found: boolean | null) => void;
}

const CITED_CLASS = "is-cited";
const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function scrollToFirst(element: HTMLElement): void {
  const behavior = window.matchMedia(REDUCED_MOTION).matches ? "auto" : "smooth";
  element.scrollIntoView({ block: "center", behavior });
}

/**
 * Una página del PDF dibujada con pdf.js a lo ancho del panel, con su capa de texto (se
 * puede seleccionar y copiar) y el pasaje citado marcado sobre ella.
 */
export function PdfPage({ url, page, passage, bounds, negative, onLocated }: PdfPageProps) {
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const text = useRef<HTMLDivElement>(null);
  const width = useElementWidth(stage);
  const [state, setState] = useState<PageState>("loading");

  useEffect(() => {
    const stageElement = stage.current;
    const canvasElement = canvas.current;
    const textElement = text.current;
    if (width === 0 || stageElement === null || canvasElement === null || textElement === null) {
      return;
    }
    let cancelled = false;
    let renderTask: RenderTask | null = null;
    let textLayer: TextLayer | null = null;
    setState("loading");
    onLocated(null);

    async function draw(stageNode: HTMLDivElement, canvasNode: HTMLCanvasElement, textNode: HTMLDivElement) {
      const document = await openDocument(url);
      const pdfPage = await document.getPage(page);
      if (cancelled) {
        return;
      }
      const scale = width / pdfPage.getViewport({ scale: 1 }).width;
      const viewport = pdfPage.getViewport({ scale });
      const ratio = window.devicePixelRatio || 1;
      canvasNode.width = Math.floor(viewport.width * ratio);
      canvasNode.height = Math.floor(viewport.height * ratio);
      canvasNode.style.width = `${Math.floor(viewport.width)}px`;
      canvasNode.style.height = `${Math.floor(viewport.height)}px`;
      stageNode.style.setProperty("--total-scale-factor", String(scale));
      renderTask = pdfPage.render({ canvas: canvasNode, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      await renderTask.promise;
      textNode.replaceChildren();
      textLayer = new TextLayer({ textContentSource: pdfPage.streamTextContent(), container: textNode, viewport });
      await textLayer.render();
      if (cancelled) {
        return;
      }
      setState("ready");
      if (passage === null) {
        return;
      }
      const divs = textLayer.textDivs;
      const span = locatePassage(
        divs.map((div) => div.textContent ?? ""),
        passage,
        bounds,
      );
      onLocated(span !== null);
      if (span === null) {
        return;
      }
      divs.slice(span.first, span.last + 1).forEach((div) => div.classList.add(CITED_CLASS));
      const first = divs[span.first];
      if (first !== undefined) {
        scrollToFirst(first);
      }
    }

    draw(stageElement, canvasElement, textElement).catch((error: unknown) => {
      if (!cancelled && !(error instanceof RenderingCancelledException)) {
        setState("failed");
      }
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayer?.cancel();
    };
  }, [url, page, passage, bounds, width, onLocated]);

  return (
    <div className="page-stage" ref={stage} data-state={state} data-negative={negative || undefined}>
      <div className="page-stage__sheet">
        <canvas ref={canvas} aria-label={`Página ${page} del PDF`} role="img" />
        <div className="textLayer" ref={text} />
      </div>
      {state === "loading" && (
        <p className="page-stage__status" role="status">
          Abriendo la página {page}…
        </p>
      )}
      {state === "failed" && (
        <p className="page-stage__status page-stage__status--failed" role="alert">
          No se pudo abrir el PDF. El servidor no tiene el archivo montado o la conexión se cortó; el pasaje citado sigue arriba.
        </p>
      )}
    </div>
  );
}
