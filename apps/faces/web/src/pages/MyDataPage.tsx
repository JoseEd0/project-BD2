import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router";

import { fetchPerson, forgetPerson, type Person } from "../api/faces";
import type { ApiProblem } from "../api/problems";
import { CopyCode } from "../components/CopyCode";
import { Icon } from "../components/Icon";
import { TextScreen } from "../components/TextScreen";
import { forgetStoredPerson, loadPeople, normalizeCode, rememberPerson, type StoredPerson } from "../storage/people";

const DATE = new Intl.DateTimeFormat("es", { dateStyle: "long" });
const NOT_FOUND = 404;
/** Los últimos caracteres del código que se ven sin revelarlo entero. */
const VISIBLE_CODE_TAIL = 4;

class PersonLookupError extends Error {
  constructor(readonly problem: ApiProblem) {
    super(problem.message);
  }
}

async function personOrNull(personId: string): Promise<Person | null> {
  const outcome = await fetchPerson(personId);
  if (outcome.ok) {
    return outcome.data;
  }
  if (outcome.problem.status === NOT_FOUND) {
    return null;
  }
  throw new PersonLookupError(outcome.problem);
}

type DeleteState = { readonly kind: "idle" } | { readonly kind: "confirming" } | { readonly kind: "deleting" } | { readonly kind: "failed"; readonly message: string };

function PersonCard({ stored, onForgotten }: { readonly stored: StoredPerson; readonly onForgotten: (name: string) => void }) {
  const person = useQuery({ queryKey: ["person", stored.personId], queryFn: () => personOrNull(stored.personId), retry: 1 });
  const [deletion, setDeletion] = useState<DeleteState>({ kind: "idle" });
  const titleId = useId();

  const forget = async () => {
    setDeletion({ kind: "deleting" });
    const outcome = await forgetPerson(stored.personId);
    if (!outcome.ok) {
      setDeletion({ kind: "failed", message: outcome.problem.message });
      return;
    }
    forgetStoredPerson(stored.personId);
    onForgotten(stored.displayName);
  };

  return (
    <section className="panel" aria-labelledby={titleId}>
      <h2 id={titleId}>{stored.displayName}</h2>
      {person.isPending && <p className="loading" role="status"><span className="spinner" aria-hidden="true" />Consultando tu registro…</p>}
      {person.isError && (
        <p className="notice notice-danger" role="alert">
          {person.error.message}
        </p>
      )}
      {person.data === null && (
        <p className="notice notice-warning">Este registro ya no existe: venció o se borró. Puedes quitarlo de este celular.</p>
      )}
      {person.data !== undefined && person.data !== null && (
        <dl className="facts">
          <dt>Registrado el</dt>
          <dd>{DATE.format(new Date(person.data.created_at))}</dd>
          <dt>Se borra solo el</dt>
          <dd>{DATE.format(new Date(person.data.expires_at))}</dd>
          <dt>Se guarda</dt>
          <dd>Tu nombre y {person.data.shots} huellas numéricas; ninguna foto</dd>
          <dt>Consentimiento</dt>
          <dd className="version-tag">{person.data.consent_version}</dd>
        </dl>
      )}
      <p className="muted">
        Código terminado en <span className="tabular">…{stored.personId.slice(-VISIBLE_CODE_TAIL)}</span>
      </p>
      <div className="row">
        <CopyCode code={stored.personId} />
      </div>
      {deletion.kind === "idle" && (
        <button type="button" className="button button-danger" onClick={() => setDeletion({ kind: "confirming" })}>
          <Icon name="trash" />
          {person.data === null ? "Quitar de este celular" : "Borrar mis datos"}
        </button>
      )}
      {(deletion.kind === "confirming" || deletion.kind === "deleting" || deletion.kind === "failed") && (
        <div className="notice notice-danger" role="group" aria-label="Confirmar el borrado">
          <p>
            <strong>¿Borrar a {stored.displayName}?</strong> Se borran tu nombre y tus huellas del servicio. No se puede deshacer.
          </p>
          {deletion.kind === "failed" && (
            <p className="field-error" role="alert">
              {deletion.message}
            </p>
          )}
          <div className="row">
            <button type="button" className="button button-danger-solid" onClick={() => void forget()} disabled={deletion.kind === "deleting"}>
              {deletion.kind === "deleting" ? "Borrando…" : "Sí, borrar"}
            </button>
            <button type="button" className="button button-quiet" onClick={() => setDeletion({ kind: "idle" })} disabled={deletion.kind === "deleting"}>
              Cancelar
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function AddCodeForm({ onAdded }: { readonly onAdded: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const errorId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const code = normalizeCode(value);
    if (code === null) {
      setError("El código tiene 32 caracteres, números y letras de la a a la f.");
      return;
    }
    const outcome = await fetchPerson(code);
    if (!outcome.ok) {
      setError(outcome.problem.message);
      return;
    }
    rememberPerson({ personId: code, displayName: outcome.data.display_name, expiresAt: outcome.data.expires_at });
    setValue("");
    setError(null);
    onAdded();
  };
  return (
    <form className="field" onSubmit={(event) => void submit(event)} noValidate>
      <label htmlFor={inputId}>¿Te registraste en otro equipo? Pega tu código</label>
      <input
        id={inputId}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="32 letras y números, como 3f9a…"
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        inputMode="text"
        aria-invalid={error !== null}
        aria-describedby={error === null ? undefined : errorId}
      />
      {error !== null && (
        <p id={errorId} className="field-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="button button-secondary">
        Buscar mi registro
      </button>
    </form>
  );
}

export default function MyDataPage() {
  const [people, setPeople] = useState(loadPeople);
  const [forgotten, setForgotten] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const refresh = () => {
    setPeople(loadPeople());
    void queryClient.invalidateQueries({ queryKey: ["person"] });
  };
  return (
    <TextScreen
      title="Mis datos"
      intro="Lo que guardamos de ti y cómo borrarlo. Tu código es la llave."
      back={{ to: "/", label: "Volver al inicio" }}
    >
      {forgotten !== null && (
        <div className="notice notice-success" role="status">
          <p>
            <strong>Listo: borramos a {forgotten}.</strong> El servicio ya no guarda su nombre ni sus huellas, y la cámara no lo reconocerá.
          </p>
        </div>
      )}
      {people.length === 0 && forgotten === null && (
        <div className="notice">
          <p>En este celular no hay ningún registro guardado.</p>
          <Link className="button button-primary" to="/registro">
            Registrarme
          </Link>
        </div>
      )}
      {people.map((stored) => (
        <PersonCard
          key={stored.personId}
          stored={stored}
          onForgotten={(name) => {
            setForgotten(name);
            setPeople(loadPeople().filter((person) => person.personId !== stored.personId));
          }}
        />
      ))}
      <AddCodeForm onAdded={refresh} />
    </TextScreen>
  );
}
