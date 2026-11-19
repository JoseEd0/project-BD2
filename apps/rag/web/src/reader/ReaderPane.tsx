import { useCallback, useMemo, useState } from "react";

import { apiUrl } from "../api/client";
import type { PageResponse } from "../api/types";
import { Icon } from "../components/Icon";
import { formatAuthors, formatPages } from "../format";
import type { ReaderTarget } from "../research/readerTarget";
import { usePage } from "../research/useRetrieval";
import { FicheGrid, PageStepper } from "./FicheGrid";
import { PdfPage } from "./PdfPage";

const PDF_MEDIA_TYPE = "application/pdf";
const LICENSE_NAMES: Readonly<Record<string, string>> = {
  "http://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
  "http://creativecommons.org/licenses/by-sa/4.0/": "CC BY-SA 4.0",
  "http://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
};

interface ReaderPaneProps {
  readonly target: ReaderTarget;
  readonly citedPages: ReadonlySet<number>;
  readonly onPage: (page: number) => void;
}

function pagesOf(start: number, end: number): Set<number> {
  return new Set(Array.from({ length: end - start + 1 }, (_, index) => start + index));
}

function PassageQuote({ target, located }: { readonly target: ReaderTarget; readonly located: boolean | null }) {
  return (
    <figure className="passage">
      <figcaption>
        {target.n === null ? "Fragmento" : <span className="key key--static">{target.n}</span>}
        <span>{formatPages(target.pageStart, target.pageEnd)}</span>
        {located === false && <span className="passage__note">No se pudo ubicar en el dibujo de la página: el texto del PDF difiere del extraído.</span>}
      </figcaption>
      <blockquote>{target.text}</blockquote>
    </figure>
  );
}

function TextDocument({ page, chunkId }: { readonly page: PageResponse; readonly chunkId: string }) {
  return (
    <div className="text-document">
      <p className="text-document__note">Este documento es texto, no un PDF: se muestran sus fragmentos de la sección {page.page}.</p>
      {page.passages.map((passage) => (
        <p key={passage.chunk_id} className="text-document__passage" data-cited={passage.chunk_id === chunkId || undefined}>
          {passage.text}
        </p>
      ))}
    </div>
  );
}

/** El paper de una cita abierto en su página, con el pasaje a la vista y marcado. */
export default function ReaderPane({ target, citedPages, onPage }: ReaderPaneProps) {
  const { paper } = target;
  const page = usePage(paper.paper_id, target.page);
  const [negative, setNegative] = useState(false);
  const [located, setLocated] = useState<boolean | null>(null);
  const onLocated = useCallback((found: boolean | null) => setLocated(found), []);
  const passagePages = useMemo(() => pagesOf(target.pageStart, target.pageEnd), [target.pageStart, target.pageEnd]);
  const onPassagePage = passagePages.has(target.page);
  const bounds = useMemo(
    () => ({ startsBefore: target.page > target.pageStart, endsAfter: target.page < target.pageEnd }),
    [target.page, target.pageStart, target.pageEnd],
  );
  const isPdf = paper.media_type === PDF_MEDIA_TYPE;
  const license = paper.license === null ? null : (LICENSE_NAMES[paper.license] ?? "licencia");
  const fileUrl = page.data === undefined ? null : apiUrl(page.data.file_url);

  return (
    <section className="reader" aria-labelledby="reader-title">
      <header className="reader__head">
        <h2 id="reader-title">{paper.title}</h2>
        <p className="reader__byline">
          {[formatAuthors(paper.authors), paper.year].filter((part) => part !== null && part !== "").join(" · ")}
          {license !== null && paper.license !== null && (
            <>
              {" · "}
              <a href={paper.license} target="_blank" rel="noreferrer">
                {license}
              </a>
            </>
          )}
          {paper.url !== null && (
            <>
              {" · "}
              <a href={paper.url} target="_blank" rel="noreferrer">
                arXiv
              </a>
            </>
          )}
        </p>
        <div className="reader__tools">
          {isPdf && (
            <button type="button" className="button button--quiet" aria-pressed={negative} onClick={() => setNegative((value) => !value)}>
              <Icon name="invert" />
              Negativo
            </button>
          )}
          {fileUrl !== null && (
            <a className="button button--quiet" href={`${fileUrl}#page=${target.page}`} target="_blank" rel="noreferrer">
              <Icon name="external" />
              Abrir el archivo
            </a>
          )}
        </div>
      </header>
      <PassageQuote target={target} located={onPassagePage ? located : null} />
      {page.data !== undefined && (
        <>
          <PageStepper pages={page.data.pages} current={target.page} onSelect={onPage} />
          <FicheGrid pages={page.data.pages} current={target.page} cited={citedPages} passagePages={passagePages} onSelect={onPage} />
        </>
      )}
      {page.isError && (
        <p className="reader__error" role="alert">
          El servicio no encontró la página {target.page} de este documento.
        </p>
      )}
      {page.data !== undefined && fileUrl !== null && isPdf && (
        <PdfPage url={fileUrl} page={target.page} passage={onPassagePage ? target.text : null} bounds={bounds} negative={negative} onLocated={onLocated} />
      )}
      {page.data !== undefined && !isPdf && <TextDocument page={page.data} chunkId={target.chunkId} />}
    </section>
  );
}
