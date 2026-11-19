import type { ReactNode } from "react";
import { Link } from "react-router";

import { Icon } from "./Icon";

export interface TextScreenProps {
  readonly title: string;
  readonly intro?: ReactNode;
  /** A dónde lleva «volver»; sin él, la barra lleva la marca. */
  readonly back?: { readonly to: string; readonly label: string } | { readonly onClick: () => void; readonly label: string };
  readonly children: ReactNode;
  readonly actions?: ReactNode;
  readonly documentTitle?: string;
}

function BackControl({ back }: { readonly back: NonNullable<TextScreenProps["back"]> }) {
  if ("to" in back) {
    return (
      <Link className="icon-button" to={back.to} aria-label={back.label}>
        <Icon name="back" />
      </Link>
    );
  }
  return (
    <button type="button" className="icon-button" onClick={back.onClick} aria-label={back.label}>
      <Icon name="back" />
    </button>
  );
}

/** Las pantallas de lectura: la banda de cortina con el título y una columna legible. */
export function TextScreen({ title, intro, back, children, actions, documentTitle }: TextScreenProps) {
  return (
    <div className="text-screen">
      <title>{documentTitle ?? `${title} · Rostros`}</title>
      <header className="text-screen-header">
        <div className="text-screen-bar">
          {back === undefined ? (
            <Link className="brand" to="/">
              <Icon name="scan" />
              Rostros
            </Link>
          ) : (
            <BackControl back={back} />
          )}
        </div>
        <div className="text-screen-heading">
          <h1 id="titulo">{title}</h1>
          {intro !== undefined && <p>{intro}</p>}
        </div>
      </header>
      <main id="contenido" className="text-screen-body" aria-labelledby="titulo">
        {children}
      </main>
      {actions ? <div className="text-screen-actions">{actions}</div> : null}
    </div>
  );
}
