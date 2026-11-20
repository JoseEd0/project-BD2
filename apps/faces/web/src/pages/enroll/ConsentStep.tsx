import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { fetchConsent, type Consent } from "../../api/faces";
import { TextScreen } from "../../components/TextScreen";

async function consentOrThrow(): Promise<Consent> {
  const outcome = await fetchConsent();
  if (!outcome.ok) {
    throw new Error(outcome.problem.message);
  }
  return outcome.data;
}

export interface ConsentStepProps {
  readonly notice: string | null;
  readonly onAccept: (version: string) => void;
}

/** El texto que sirve el servicio, tal cual y con su versión; nada se crea sin aceptarlo. */
export function ConsentStep({ notice, onAccept }: ConsentStepProps) {
  const consent = useQuery({ queryKey: ["consent"], queryFn: consentOrThrow, retry: 1, staleTime: 0 });
  const [accepted, setAccepted] = useState(false);
  const checkboxId = useId();
  const data = consent.data;

  return (
    <TextScreen
      title="Antes de empezar"
      intro="Lee qué guardamos y para qué. Sin tu sí no se guarda nada."
      back={{ to: "/", label: "Volver al inicio" }}
      actions={
        data !== undefined && (
          <>
            <label className="checkbox checkbox-compact" htmlFor={checkboxId}>
              <input id={checkboxId} type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
              Leí el texto y acepto que se guarde lo que describe.
            </label>
            <button
              type="button"
              className="button button-primary button-block"
              disabled={!accepted}
              onClick={() => onAccept(data.version)}
            >
              Aceptar y continuar
            </button>
          </>
        )
      }
    >
      {notice !== null && (
        <p className="notice notice-warning" role="alert">
          {notice}
        </p>
      )}
      {consent.isPending && (
        <div className="panel" role="status" aria-label="Cargando el consentimiento">
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}
      {consent.isError && (
        <div className="notice notice-danger" role="alert">
          <p>No pudimos traer el texto del consentimiento. {consent.error.message}</p>
          <button type="button" className="button button-secondary" onClick={() => void consent.refetch()}>
            Reintentar
          </button>
        </div>
      )}
      {data !== undefined && (
        <article className="panel consent" aria-labelledby="consent-title">
          <h2 id="consent-title">{data.title}</h2>
          <section>
            <h2>Para qué</h2>
            <p>{data.purpose}</p>
          </section>
          <section>
            <h2>Qué se guarda</h2>
            <ul>
              {data.stored_data.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
          <section>
            <h2>Qué no se guarda</h2>
            <p>{data.not_stored}</p>
          </section>
          <section>
            <h2>Cuánto tiempo</h2>
            <p>{data.retention}</p>
          </section>
          <section>
            <h2>Tus derechos</h2>
            <p>{data.rights}</p>
          </section>
          <p className="version-tag">Versión {data.version}</p>
        </article>
      )}
    </TextScreen>
  );
}
