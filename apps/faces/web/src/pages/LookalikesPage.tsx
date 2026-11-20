import { useMutation } from "@tanstack/react-query";
import { useId, type ChangeEvent } from "react";

import { findLookalikes, lookalikePhotoUrl, type FamousLookalike, type Lookalikes } from "../api/faces";
import type { ApiProblem } from "../api/problems";
import { Icon } from "../components/Icon";
import { TextScreen } from "../components/TextScreen";

class LookalikesError extends Error {
  constructor(readonly problem: ApiProblem) {
    super(problem.message);
  }
}

async function lookalikesOf(photo: Blob): Promise<Lookalikes> {
  const outcome = await findLookalikes(photo);
  if (!outcome.ok) {
    throw new LookalikesError(outcome.problem);
  }
  return outcome.data;
}

function LookalikeCard({ person, rank }: { readonly person: FamousLookalike; readonly rank: number }) {
  return (
    <li className="lookalike">
      <img src={lookalikePhotoUrl(person)} alt={`Foto de ${person.name} en LFW`} width={96} height={96} loading="lazy" />
      <div className="lookalike-body">
        <strong>
          <span className="lookalike-rank tabular">{rank}.</span> {person.name}
        </strong>
        <span className="lookalike-meter" aria-hidden="true">
          <span style={{ width: `${person.percent}%` }} />
        </span>
        <span className="muted tabular">
          {person.percent} % de parecido · coseno {person.similarity.toFixed(2)}
        </span>
      </div>
    </li>
  );
}

export default function LookalikesPage() {
  const inputId = useId();
  const search = useMutation({ mutationFn: lookalikesOf });
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const photo = event.target.files?.[0];
    event.target.value = "";
    if (photo !== undefined) {
      search.mutate(photo);
    }
  };
  return (
    <TextScreen
      title="¿A qué famoso te pareces?"
      intro="Tómate una selfie o elige una foto. Te comparamos solo con las personas públicas de LFW, nunca con quienes se registraron, y tu foto no se guarda."
      back={{ to: "/", label: "Volver al inicio" }}
    >
      <input id={inputId} className="visually-hidden photo-picker" type="file" accept="image/*" capture="user" onChange={choose} disabled={search.isPending} />
      <label htmlFor={inputId} className="button button-primary">
        <Icon name="camera" />
        {search.isPending ? "Buscando…" : "Tomar o elegir una foto"}
      </label>
      {search.isPending && (
        <p className="loading" role="status">
          <span className="spinner" aria-hidden="true" />
          Buscando en el HNSW del gestor…
        </p>
      )}
      {search.isError && (
        <p className="notice notice-danger" role="alert">
          {search.error.message}
        </p>
      )}
      {search.data !== undefined && (
        <section className="panel" aria-live="polite">
          <p>{search.data.message}</p>
          {search.data.people.length > 0 && (
            <ol className="lookalikes">
              {search.data.people.map((person, index) => (
                <LookalikeCard key={person.id} person={person} rank={index + 1} />
              ))}
            </ol>
          )}
        </section>
      )}
      <p className="muted">Labeled Faces in the Wild (Huang et al., UMass Amherst, 2007): 13 233 fotos de prensa de 5 749 personas.</p>
    </TextScreen>
  );
}
