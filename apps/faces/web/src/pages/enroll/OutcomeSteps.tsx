import { Link } from "react-router";

import type { Person } from "../../api/faces";
import type { ApiProblem } from "../../api/problems";
import { CopyCode } from "../../components/CopyCode";
import { TextScreen } from "../../components/TextScreen";
import type { BlockReason } from "../../enrollment/machine";
import { groupCode } from "../../storage/people";

const DATE = new Intl.DateTimeFormat("es", { dateStyle: "long" });

export function FinishingStep({ problem, onRetry }: { readonly problem: ApiProblem | null; readonly onRetry: () => void }) {
  return (
    <TextScreen
      title={problem === null ? "Guardando tu registro…" : "No pudimos terminar"}
      back={{ to: "/", label: "Volver al inicio" }}
      actions={
        problem !== null && (
          <button type="button" className="button button-primary button-block" onClick={onRetry}>
            Reintentar
          </button>
        )
      }
    >
      {problem === null ? (
        <p className="loading" role="status">
          <span className="spinner" aria-hidden="true" />
          Tenemos tus tres tomas. Revisamos que no estés registrado ya y te guardamos.
        </p>
      ) : (
        <p className="notice notice-danger" role="alert">
          {problem.message}
        </p>
      )}
    </TextScreen>
  );
}

export interface DoneStepProps {
  readonly person: Person;
  readonly message: string;
  readonly saved: boolean;
}

/** El código se muestra esta única vez: es la llave para borrar el registro. */
export function DoneStep({ person, message, saved }: DoneStepProps) {
  return (
    <TextScreen
      title={`Listo, ${person.display_name}`}
      intro={message}
      back={{ to: "/", label: "Volver al inicio" }}
      actions={
        <>
          <Link className="button button-primary button-block" to="/reconocer">
            Probar ahora
          </Link>
          <Link className="button button-quiet button-block" to="/">
            Ir al inicio
          </Link>
        </>
      }
    >
      <section className="panel" aria-labelledby="code-title">
        <h2 id="code-title">Tu código para borrar tus datos</h2>
        <p className="code" data-testid="person-code">
          {groupCode(person.person_id)}
        </p>
        <CopyCode code={person.person_id} />
        <p className="muted">
          Guárdalo: es la única forma de ver o borrar tu registro desde otro equipo, y no lo volveremos a mostrar.{" "}
          {saved ? "En este celular queda guardado en «Mis datos»." : "Este navegador no deja guardarlo: cópialo ahora."}
        </p>
      </section>
      <dl className="facts">
        <dt>Tomas guardadas</dt>
        <dd>{person.shots} huellas numéricas, ninguna foto</dd>
        <dt>Se borra solo el</dt>
        <dd>{DATE.format(new Date(person.expires_at))}</dd>
      </dl>
    </TextScreen>
  );
}

const BLOCK_TITLES: Record<BlockReason, string> = {
  duplicate: "Ya estás registrado",
  lookalike: "No podemos registrarte",
  expired: "La sesión de registro venció",
  attempts: "Se acabaron los intentos",
};

export function BlockedStep({ reason, message, onRestart }: { readonly reason: BlockReason; readonly message: string; readonly onRestart: () => void }) {
  return (
    <TextScreen
      title={BLOCK_TITLES[reason]}
      back={{ to: "/", label: "Volver al inicio" }}
      actions={
        reason === "duplicate" ? (
          <>
            <Link className="button button-primary button-block" to="/reconocer">
              Probar el reconocimiento
            </Link>
            <Link className="button button-quiet button-block" to="/mis-datos">
              Mis datos
            </Link>
          </>
        ) : (
          <button type="button" className="button button-primary button-block" onClick={onRestart}>
            Empezar de nuevo
          </button>
        )
      }
    >
      <p className="notice notice-warning" role="alert">
        {message}
      </p>
      {reason === "duplicate" && <p className="lead">Para registrarte otra vez, primero borra tu registro anterior con su código.</p>}
      {reason === "lookalike" && <p className="lead">Las tomas se descartaron. Si crees que es un error, prueba con mejor luz.</p>}
      {reason === "attempts" && <p className="lead">Busca un lugar con luz pareja de frente, sin contraluz, y vuelve a empezar.</p>}
    </TextScreen>
  );
}
