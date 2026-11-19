import { getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask, type PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerUrl;

/**
 * Los PDF abiertos, por URL. Saltar entre citas suele volver a los mismos dos o tres papers:
 * se conservan los últimos `KEPT_DOCUMENTS` y se cierra el más viejo.
 */
const KEPT_DOCUMENTS = 4;
/** pdf.js pide el archivo por rangos (`Range`): un paper largo no se baja entero para ver una página. */
const RANGE_CHUNK_BYTES = 65_536;

const opened = new Map<string, PDFDocumentLoadingTask>();

function evictOldest(): void {
  const oldest = opened.keys().next();
  if (oldest.done === true) {
    return;
  }
  const task = opened.get(oldest.value);
  opened.delete(oldest.value);
  void task?.destroy();
}

/** El documento de `url`, abierto una sola vez. Rechaza si no es un PDF o no llega. */
export function openDocument(url: string): Promise<PDFDocumentProxy> {
  const known = opened.get(url);
  if (known !== undefined) {
    opened.delete(url);
    opened.set(url, known);
    return known.promise;
  }
  const task = getDocument({ url, rangeChunkSize: RANGE_CHUNK_BYTES, disableAutoFetch: true });
  opened.set(url, task);
  task.promise.catch(() => opened.delete(url));
  if (opened.size > KEPT_DOCUMENTS) {
    evictOldest();
  }
  return task.promise;
}
