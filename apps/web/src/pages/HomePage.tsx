import { Link } from "react-router";

import { AppGlyph } from "../components/AppGlyph";
import { StageTimings } from "../components/StageTimings";
import { StatusBadge } from "../components/StatusBadge";
import { ENGINE, PORTAL_APPS, type PortalApp } from "../services/catalog";
import { useServiceHealth } from "../services/useServiceHealth";

function AppLane({ app }: { readonly app: PortalApp }) {
  const { data: health } = useServiceHealth(app);
  return (
    <li className="lane" data-app={app.id}>
      <Link to={`/${app.slug}`} className="lane-link">
        <span className="lane-glyph">
          <AppGlyph service={app.id} />
        </span>
        <span className="lane-text">
          <span className="lane-name">{app.name}</span>
          <span className="lane-summary">{app.summary}</span>
        </span>
        <StatusBadge health={health} />
      </Link>
    </li>
  );
}

function EngineFoundation() {
  const { data: health } = useServiceHealth(ENGINE);
  return (
    <section className="foundation" aria-labelledby="foundation-title">
      <div className="foundation-head">
        <span className="lane-glyph">
          <AppGlyph service={ENGINE.id} />
        </span>
        <div>
          <h2 id="foundation-title">{ENGINE.name}</h2>
          <p>Las cuatro guardan y buscan sus datos aquí, y solo a través de su API.</p>
        </div>
        <StatusBadge health={health} />
      </div>
      {health?.state === "available" && (
        <StageTimings serverTiming={health.serverTiming} caption="Última comprobación de salud" />
      )}
    </section>
  );
}

export default function HomePage() {
  return (
    <div className="page home">
      <title>Aplicaciones del Minigestor</title>
      <header className="hero">
        <h1>Cuatro aplicaciones de IA sobre un gestor de base de datos hecho desde cero</h1>
        <p className="lede">
          Audio, compras, papers y rostros: cada una es un servicio aparte que se apoya en el mismo minigestor.
        </p>
      </header>
      <div className="platform">
        <ul className="app-stack" aria-label="Aplicaciones y estado de su servicio">
          {PORTAL_APPS.map((app) => (
            <AppLane key={app.id} app={app} />
          ))}
        </ul>
        <EngineFoundation />
      </div>
    </div>
  );
}
