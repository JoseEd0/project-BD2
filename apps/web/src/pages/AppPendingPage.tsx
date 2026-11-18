import { AppGlyph } from "../components/AppGlyph";
import { StatusBadge } from "../components/StatusBadge";
import { appById, type AppId } from "../services/catalog";
import { UNAVAILABLE_MESSAGES } from "../services/health";
import { useServiceHealth } from "../services/useServiceHealth";

const DOCUMENT_TITLE_SUFFIX = "Aplicaciones del Minigestor";

function ServiceState({ appId }: { readonly appId: AppId }) {
  const { data: health, refetch, isFetching } = useServiceHealth(appById(appId));
  if (health === undefined) {
    return <p className="state-text">Comprobando si su servicio responde…</p>;
  }
  if (health.state === "available") {
    return (
      <p className="state-text">
        El servicio ya responde. Su interfaz llega con la próxima versión del portal.
      </p>
    );
  }
  return (
    <>
      <p className="state-text">{UNAVAILABLE_MESSAGES[health.reason]}</p>
      <button type="button" className="button" onClick={() => void refetch()} disabled={isFetching}>
        {isFetching ? "Comprobando…" : "Volver a comprobar"}
      </button>
    </>
  );
}

/** La página de una aplicación cuyo servicio aún no tiene interfaz: qué hará y si responde. */
export function AppPendingPage({ appId }: { readonly appId: AppId }) {
  const app = appById(appId);
  const { data: health } = useServiceHealth(app);
  return (
    <article className="page app-page" data-app={app.id} aria-labelledby="app-title">
      <title>{`${app.name} | ${DOCUMENT_TITLE_SUFFIX}`}</title>
      <header className="app-header">
        <span className="app-header-glyph">
          <AppGlyph service={app.id} />
        </span>
        <h1 id="app-title">{app.name}</h1>
        <p className="lede">{app.summary}</p>
      </header>
      <div className="app-body">
        <section aria-labelledby="capabilities-title">
          <h2 id="capabilities-title">Qué podrás hacer</h2>
          <ul className="capabilities">
            {app.capabilities.map((capability) => (
              <li key={capability}>{capability}</li>
            ))}
          </ul>
        </section>
        <section className="empty-state" aria-labelledby="service-title" aria-live="polite">
          <div className="empty-state-head">
            <h2 id="service-title">Su servicio</h2>
            <StatusBadge health={health} />
          </div>
          <ServiceState appId={appId} />
        </section>
      </div>
    </article>
  );
}
