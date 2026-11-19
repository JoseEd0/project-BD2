import { useId, useState, type FormEvent } from "react";

import { NAME_MAX_LENGTH } from "../../enrollment/machine";
import { TextScreen } from "../../components/TextScreen";

export interface NameStepProps {
  readonly initialName: string;
  readonly error: string | null;
  readonly starting: boolean;
  readonly onSubmit: (name: string) => void;
  readonly onBack: () => void;
}

export function NameStep({ initialName, error, starting, onSubmit, onBack }: NameStepProps) {
  const [name, setName] = useState(initialName);
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit(name);
  };
  return (
    <TextScreen
      title="¿Cómo te llamas?"
      intro="Es lo que aparecerá sobre tu cara cuando la cámara te reconozca."
      back={{ onClick: onBack, label: "Volver al consentimiento" }}
      actions={
        <button type="submit" form="name-form" className="button button-primary button-block" disabled={starting}>
          {starting ? <span className="spinner" aria-hidden="true" /> : null}
          {starting ? "Preparando…" : "Abrir la cámara"}
        </button>
      }
    >
      <form id="name-form" className="field" onSubmit={submit} noValidate>
        <label htmlFor={inputId}>Tu nombre</label>
        <input
          id={inputId}
          name="display_name"
          type="text"
          autoComplete="given-name"
          autoCapitalize="words"
          enterKeyHint="go"
          maxLength={NAME_MAX_LENGTH}
          value={name}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={error !== null}
          aria-describedby={error === null ? hintId : `${errorId} ${hintId}`}
          autoFocus
        />
        {error !== null && (
          <p id={errorId} className="field-error" role="alert">
            {error}
          </p>
        )}
        <p id={hintId} className="field-hint">
          Hasta {NAME_MAX_LENGTH} caracteres. Puede ser solo tu nombre de pila.
        </p>
      </form>
      <div className="notice">
        <strong>Lo que viene</strong>
        <p>Tres tomas: de frente, girando un poco a tu izquierda y a tu derecha. Se toman solas cuando tu cara está en el óvalo.</p>
      </div>
    </TextScreen>
  );
}
