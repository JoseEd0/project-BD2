import { useMutation } from "@tanstack/react-query";
import { useEffect, useId, useState, type ChangeEvent } from "react";

import { findLookalikes, lookalikePhotoUrl, type FamousLookalike, type Lookalikes } from "../api/faces";
import type { ApiProblem } from "../api/problems";
import { Icon } from "../components/Icon";
import { SelfieCamera } from "../components/SelfieCamera";
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

/** La foto enviada, para verla junto a los resultados; su URL se libera al cambiarla. */
function usePhotoPreview(photo: Blob | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (photo === null) {
      setUrl(null);
      return;
    }
    const created = URL.createObjectURL(photo);
    setUrl(created);
    return () => URL.revokeObjectURL(created);
  }, [photo]);
  return url;
}

export default function LookalikesPage() {
  const inputId = useId();
  const search = useMutation({ mutationFn: lookalikesOf });
  const [cameraOpen, setCameraOpen] = useState(false);
  const [photo, setPhoto] = useState<Blob | null>(null);
  const preview = usePhotoPreview(photo);
  const send = (chosen: Blob) => {
    setPhoto(chosen);
    search.mutate(chosen);
  };
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0];
    event.target.value = "";
    if (chosen !== undefined) {
      send(chosen);
    }
  };

  if (cameraOpen) {
    return (
      <SelfieCamera
        title="Tu selfie"
        onCancel={() => setCameraOpen(false)}
        onCapture={(captured) => {
          setCameraOpen(false);
          send(captured);
        }}
      />
    );
  }

  return (
    <TextScreen
      title="¿A qué famoso te pareces?"
      intro="Tómate una selfie o elige una foto. Te comparamos solo con las personas públicas de LFW, nunca con quienes se registraron, y tu foto no se guarda."
      back={{ to: "/", label: "Volver al inicio" }}
    >
      <div className="photo-actions">
        <button type="button" className="button button-primary" onClick={() => setCameraOpen(true)} disabled={search.isPending}>
          <Icon name="camera" />
          {photo === null ? "Tomar foto" : "Tomar otra"}
        </button>
        <input id={inputId} className="visually-hidden photo-picker" type="file" accept="image/*" onChange={choose} disabled={search.isPending} />
        <label htmlFor={inputId} className="button button-secondary">
          Elegir foto
        </label>
      </div>
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
      {search.data === undefined && !search.isPending && (
        <ol className="how-steps">
          <li>
            <p>
              <strong>Tu foto se vuelve una huella.</strong> Encontramos tu cara y la convertimos en 128 números; la foto no se guarda.
            </p>
          </li>
          <li>
            <p>
              <strong>El gestor busca a los más cercanos.</strong> Compara esa huella con las de 5 740 personas públicas en su índice HNSW.
            </p>
          </li>
          <li>
            <p>
              <strong>Ves a tus cinco parecidos.</strong> Con su foto y cuánto se parecen a ti.
            </p>
          </li>
        </ol>
      )}
      {search.data !== undefined && (
        <section className="panel" aria-live="polite">
          <div className="lookalike-query">
            {preview !== null && <img src={preview} alt="La foto que enviaste" width={72} height={72} />}
            <p>{search.data.message}</p>
          </div>
          {search.data.people.length > 0 && (
            <ol className="lookalikes">
              {search.data.people.map((person, index) => (
                <LookalikeCard key={person.id} person={person} rank={index + 1} />
              ))}
            </ol>
          )}
        </section>
      )}
      <p className="muted attribution">Labeled Faces in the Wild (Huang et al., UMass Amherst, 2007): 13 233 fotos de prensa de 5 749 personas.</p>
    </TextScreen>
  );
}
