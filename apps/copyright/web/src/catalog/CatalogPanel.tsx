import { useEffect, useState } from "react";

import { catalogStateOf } from "../analysis/catalog";
import { formatClock } from "../analysis/format";
import { CatalogProblem, useCatalogSummary, useSongs } from "../api/catalog";
import { songAudioUrl } from "../api/client";
import type { Song } from "../api/types";
import { Icon } from "../components/Icon";
import { usePlayer } from "../player/usePlayer";

const SEARCH_DELAY_MS = 250;
const NUMBER = new Intl.NumberFormat("es");

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}

function CatalogStatus() {
  const summary = useCatalogSummary();
  if (summary.isPending) {
    return <p className="catalog__status">Leyendo el catálogo…</p>;
  }
  if (summary.isError) {
    const problem = summary.error instanceof CatalogProblem ? summary.error.problem : null;
    return (
      <div className="catalog__notice" role="status">
        <strong>{problem?.title ?? "No pudimos leer el catálogo"}</strong>
        <span>{problem?.detail ?? "Vuelve a intentarlo en un momento."}</span>
        <button type="button" className="button button--quiet" onClick={() => void summary.refetch()} disabled={summary.isFetching}>
          <Icon name="retry" />
          Reintentar
        </button>
      </div>
    );
  }
  const state = catalogStateOf(summary.data);
  if (state === "empty") {
    return (
      <div className="catalog__notice" role="status">
        <strong>El catálogo está vacío</strong>
        <span>La carga de las canciones originales todavía no termina. Esta lista se actualiza sola.</span>
      </div>
    );
  }
  if (state === "indexing") {
    return (
      <div className="catalog__notice" role="status">
        <strong>El catálogo se está cargando</strong>
        <span>
          Ya puedes escuchar las {NUMBER.format(summary.data.songs)} canciones; la búsqueda de parecidas se activa cuando terminen de indexarse
          sus tramos.
        </span>
      </div>
    );
  }
  return (
    <p className="catalog__status">
      {NUMBER.format(summary.data.songs)} canciones en {NUMBER.format(summary.data.segments)} tramos de {summary.data.window_seconds} s
    </p>
  );
}

interface SongRowProps {
  readonly song: Song;
  readonly chosen: boolean;
  readonly playing: boolean;
  readonly canSearch: boolean;
  readonly onPreview: (song: Song) => void;
  readonly onFindSimilar: (song: Song) => void;
}

function SongRow({ song, chosen, playing, canSearch, onPreview, onFindSimilar }: SongRowProps) {
  return (
    <li className="song" data-chosen={chosen}>
      <button type="button" className="song__play" onClick={() => onPreview(song)} aria-label={playing ? `Pausar ${song.title}` : `Escuchar ${song.title}`}>
        <Icon name={playing ? "pause" : "play"} />
      </button>
      <div className="song__text">
        <span className="song__title">{song.title}</span>
        <span className="song__meta">
          {song.artist} <span className="song__genre">{song.genre}</span> <span className="measure">{formatClock(song.duration_seconds)}</span>
        </span>
      </div>
      <button type="button" className="button button--find" onClick={() => onFindSimilar(song)} disabled={!canSearch}>
        <Icon name="similar" />
        Buscar parecidas
      </button>
    </li>
  );
}

interface CatalogPanelProps {
  readonly chosenId: number | null;
  readonly onFindSimilar: (song: Song) => void;
}

/** El catálogo de canciones originales: buscar por título o artista, escuchar y elegir una. */
export function CatalogPanel({ chosenId, onFindSimilar }: CatalogPanelProps) {
  const [search, setSearch] = useState("");
  const songs = useSongs(useDebounced(search, SEARCH_DELAY_MS));
  const summary = useCatalogSummary();
  const canSearch = summary.data !== undefined && catalogStateOf(summary.data) === "ready";
  const [previewing, setPreviewing] = useState<Song | null>(null);
  const preview = usePlayer(previewing === null ? null : songAudioUrl(previewing.id));

  useEffect(() => {
    if (previewing !== null) {
      preview.audioRef.current?.play().catch(() => setPreviewing(null));
    }
  }, [previewing, preview.audioRef]);

  const togglePreview = (song: Song) => {
    if (previewing?.id === song.id) {
      preview.toggle();
      return;
    }
    setPreviewing(song);
  };

  return (
    <aside className="catalog" aria-labelledby="catalog-title">
      <h2 id="catalog-title" className="catalog__title">
        Catálogo
      </h2>
      <CatalogStatus />
      <label className="catalog__search">
        <Icon name="search" />
        <span className="visually-hidden">Buscar en el catálogo</span>
        <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Título o artista" />
      </label>
      {songs.isPending && <p className="catalog__status">Cargando canciones…</p>}
      {songs.isError && (
        <p className="catalog__status">{songs.error instanceof CatalogProblem ? songs.error.problem.title : "No pudimos leer las canciones"}.</p>
      )}
      {songs.data !== undefined && songs.data.length === 0 && (
        <p className="catalog__status">{search.trim() === "" ? "Todavía no hay canciones cargadas." : `Ninguna canción responde a «${search.trim()}».`}</p>
      )}
      {songs.data !== undefined && songs.data.length > 0 && (
        <ul className="catalog__list" aria-label="Canciones del catálogo">
          {songs.data.map((song) => (
            <SongRow
              key={song.id}
              song={song}
              chosen={song.id === chosenId}
              playing={previewing?.id === song.id && preview.playing}
              canSearch={canSearch}
              onPreview={togglePreview}
              onFindSimilar={onFindSimilar}
            />
          ))}
        </ul>
      )}
      <audio ref={preview.audioRef} src={previewing === null ? undefined : songAudioUrl(previewing.id)} preload="none" data-group="preview" />
    </aside>
  );
}
