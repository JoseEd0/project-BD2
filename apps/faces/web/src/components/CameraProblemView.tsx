import type { ReactNode } from "react";
import { Link } from "react-router";

import { browserOf, permissionSteps, secureAddressFor, type CameraProblem } from "../camera/problems";
import { SECURE_PORT_BY_HTTP_PORT } from "../config";
import { Icon, type IconName } from "./Icon";

interface ProblemCopy {
  readonly icon: IconName;
  readonly title: string;
  readonly body: string;
  readonly steps: readonly string[];
  readonly canRetry: boolean;
}

function copyFor(problem: CameraProblem): ProblemCopy {
  switch (problem) {
    case "insecure":
      return {
        icon: "lock",
        title: "Abre la app por HTTPS",
        body: "El navegador solo deja usar la cámara en una dirección segura, que empieza con https://.",
        steps: [
          "Si te compartieron un enlace que termina en trycloudflare.com, ábrelo tal cual.",
          "En la red del aula, usa la dirección https:// del servidor; la primera vez acepta el aviso del certificado.",
          "Escribir http:// a mano no sirve: la cámara queda bloqueada.",
        ],
        canRetry: false,
      };
    case "unsupported":
      return {
        icon: "cameraOff",
        title: "Este navegador no puede usar la cámara",
        body: "Pasa a menudo con el navegador que se abre dentro de otra app (WhatsApp, Instagram).",
        steps: ["Abre este enlace en Safari (iPhone) o en Chrome (Android).", "Si ya estás ahí, actualiza el navegador."],
        canRetry: false,
      };
    case "denied":
      return {
        icon: "cameraOff",
        title: "Permite el uso de la cámara",
        body: "Sin la cámara no podemos registrarte ni reconocerte. No guardamos fotos: cada cuadro se analiza y se descarta.",
        steps: permissionSteps(browserOf(navigator.userAgent)),
        canRetry: true,
      };
    case "not-found":
      return {
        icon: "cameraOff",
        title: "No encontramos una cámara",
        body: "Este equipo no tiene cámara o el sistema no la muestra.",
        steps: ["Conecta una cámara o abre la app desde tu celular.", "Luego toca «Intentar de nuevo»."],
        canRetry: true,
      };
    case "in-use":
      return {
        icon: "cameraOff",
        title: "La cámara está ocupada",
        body: "Otra app la está usando o el sistema no pudo abrirla.",
        steps: ["Cierra videollamadas y la app de cámara.", "Vuelve aquí y toca «Intentar de nuevo»."],
        canRetry: true,
      };
    case "unknown":
      return {
        icon: "alert",
        title: "No pudimos abrir la cámara",
        body: "Algo falló al pedirla al navegador.",
        steps: ["Toca «Intentar de nuevo».", "Si sigue fallando, recarga la página."],
        canRetry: true,
      };
  }
}

export interface CameraProblemViewProps {
  readonly problem: CameraProblem;
  readonly onRetry: () => void;
  readonly extra?: ReactNode;
}

/** Qué pasó con la cámara y cómo salir de ahí, sobre la cortina. */
export function CameraProblemView({ problem, onRetry, extra }: CameraProblemViewProps) {
  const copy = copyFor(problem);
  const secureAddress = problem === "insecure" ? secureAddressFor(window.location, SECURE_PORT_BY_HTTP_PORT) : null;
  return (
    <section className="camera-problem" aria-labelledby="camera-problem-title" role="alert">
      <div className="camera-problem-icon">
        <Icon name={copy.icon} size={28} />
      </div>
      <h1 id="camera-problem-title">{copy.title}</h1>
      <p>{copy.body}</p>
      <ol>
        {copy.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {problem === "insecure" && (
        <div className="stack">
          <p>Estás en:</p>
          <p className="code">{window.location.href}</p>
        </div>
      )}
      {secureAddress !== null && (
        <a className="button button-primary" href={secureAddress}>
          Abrir la dirección segura
        </a>
      )}
      {copy.canRetry && (
        <button type="button" className="button button-primary" onClick={onRetry}>
          <Icon name="refresh" />
          Intentar de nuevo
        </button>
      )}
      {extra}
      <Link className="button button-quiet" to="/">
        Volver al inicio
      </Link>
    </section>
  );
}
