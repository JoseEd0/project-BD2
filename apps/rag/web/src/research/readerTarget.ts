import type { Paper, SearchHit } from "../api/types";
import type { AnswerSource } from "./answerEvents";
import { citedNumbers } from "./citations";
import type { Turn } from "./turns";

/** Lo que el lector muestra: un fragmento de un paper, abierto en una de sus páginas. */
export interface ReaderTarget {
  readonly paper: Paper;
  readonly chunkId: string;
  readonly text: string;
  readonly pageStart: number;
  readonly pageEnd: number;
  /** La página abierta: empieza en la del fragmento y cambia al hojear. */
  readonly page: number;
  /** El número de la cita, si se abrió desde una; `null` desde el panel de recuperación. */
  readonly n: number | null;
}

export function targetOfSource(source: AnswerSource): ReaderTarget {
  return {
    paper: source.paper,
    chunkId: source.chunk_id,
    text: source.text,
    pageStart: source.page_start,
    pageEnd: source.page_end,
    page: source.page_start,
    n: source.n,
  };
}

export function targetOfHit(hit: SearchHit, sentAs: number | null): ReaderTarget {
  return {
    paper: hit.paper,
    chunkId: hit.chunk_id,
    text: hit.text,
    pageStart: hit.page_start,
    pageEnd: hit.page_end,
    page: hit.page_start,
    n: sentAs,
  };
}

/** Las páginas de `paperId` que la respuesta cita, para marcarlas en la ficha del paper. */
export function citedPages(turn: Turn, paperId: string): Set<number> {
  const cited = citedNumbers(turn.text, turn.sources.length);
  const pages = new Set<number>();
  for (const source of turn.sources) {
    if (source.paper.paper_id !== paperId || !cited.has(source.n)) {
      continue;
    }
    for (let page = source.page_start; page <= source.page_end; page += 1) {
      pages.add(page);
    }
  }
  return pages;
}
