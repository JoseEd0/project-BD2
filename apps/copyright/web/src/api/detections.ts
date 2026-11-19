import { REQUEST_ID_HEADER, SERVER_TIMING_HEADER } from "@minigestor/web-kit";

import { problemOf, unreachableProblem, type Problem } from "../analysis/problem";
import { API_BASE_URL } from "../config";
import type { Detection } from "./types";

export const DETECTIONS_URL = `${API_BASE_URL}/detections`;
const UPLOAD_FIELD = "file";
const FIRST_ERROR_STATUS = 400;

export interface DetectionResult {
  readonly detection: Detection;
  readonly serverTiming: string | null;
}

/** La subida terminó sin detección: el motivo, listo para mostrarse. */
export class DetectionProblem extends Error {
  constructor(readonly problem: Problem) {
    super(problem.title);
    this.name = "DetectionProblem";
  }
}

export interface UploadCallbacks {
  /** Fracción subida, entre 0 y 1; `null` si el navegador no sabe el tamaño total. */
  readonly onProgress: (share: number | null) => void;
  /** El archivo ya subió entero: el servicio está analizando. */
  readonly onUploaded: () => void;
  readonly signal: AbortSignal;
}

function parsedBody(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/**
 * Sube `file` a `POST /detections` con `XMLHttpRequest`, el único que informa del progreso
 * de una subida. Se rechaza con `DetectionProblem` si el API no da una detección y con un
 * `DOMException` `AbortError` si se cancela.
 */
export function uploadForDetection(file: File, callbacks: UploadCallbacks): Promise<DetectionResult> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const abort = () => request.abort();
    request.open("POST", DETECTIONS_URL);
    request.setRequestHeader(REQUEST_ID_HEADER, crypto.randomUUID());
    request.upload.onprogress = (event) => callbacks.onProgress(event.lengthComputable ? event.loaded / event.total : null);
    request.upload.onload = () => callbacks.onUploaded();
    request.onload = () => {
      callbacks.signal.removeEventListener("abort", abort);
      const body = parsedBody(request.responseText);
      if (request.status >= FIRST_ERROR_STATUS) {
        reject(new DetectionProblem(problemOf(request.status, body)));
        return;
      }
      resolve({ detection: body as Detection, serverTiming: request.getResponseHeader(SERVER_TIMING_HEADER) });
    };
    request.onerror = () => reject(new DetectionProblem(unreachableProblem()));
    request.onabort = () => reject(new DOMException("subida cancelada", "AbortError"));
    callbacks.signal.addEventListener("abort", abort, { once: true });
    const form = new FormData();
    form.append(UPLOAD_FIELD, file, file.name);
    request.send(form);
  });
}
