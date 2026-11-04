import { Suspense, lazy, useEffect, useState } from "react";
import type { ComponentType, LazyExoticComponent } from "react";

import StatusBar from "./components/StatusBar";
import TopBar from "./components/TopBar";
import { useHashRoute } from "./hooks/useHashRoute";
import { useWorkspace } from "./hooks/useWorkspace";
import type { Workspace } from "./hooks/useWorkspace";
import type { PageId } from "./routes";

interface PageProps {
  workspace: Workspace;
}

/** Cada página es un trozo aparte del bundle: solo se descarga la primera vez que se abre. */
const PAGE_COMPONENTS: Record<PageId, LazyExoticComponent<ComponentType<PageProps>>> = {
  consola: lazy(() => import("./pages/ConsolePage")),
  texto: lazy(() => import("./pages/TextSearchPage")),
};

/** Lo que se ve mientras llega el código de una página: la cabecera y la barra de estado
 * de siempre, para que la carga no mueva nada de sitio. */
function PageLoading({ page, workspace }: { page: PageId; workspace: Workspace }) {
  return (
    <div className="workbench">
      <TopBar
        inTransaction={workspace.inTransaction}
        onRefresh={() => void workspace.refreshTables()}
        onToggleTheme={workspace.toggleTheme}
        page={page}
        theme={workspace.theme}
      />
      <p className="panel__empty">Cargando…</p>
      <StatusBar
        connected={workspace.connected}
        elapsedMs={null}
        rowCount={null}
        tableCount={workspace.tables.length}
        totalRows={workspace.totalRows}
      />
    </div>
  );
}

function withPage(opened: ReadonlySet<PageId>, page: PageId): ReadonlySet<PageId> {
  return opened.has(page) ? opened : new Set([...opened, page]);
}

/**
 * La aplicación: el estado compartido y las páginas. Una página que ya se abrió sigue
 * montada, oculta, al pasar a otra: así la consola conserva su editor, su resultado y su
 * mapa, y la página de texto su búsqueda.
 */
export default function App() {
  const workspace = useWorkspace();
  const page = useHashRoute();
  const [opened, setOpened] = useState<ReadonlySet<PageId>>(() => new Set([page]));

  useEffect(() => {
    setOpened((previous) => withPage(previous, page));
  }, [page]);

  return (
    <>
      {[...withPage(opened, page)].map((id) => {
        const Page = PAGE_COMPONENTS[id];
        return (
          <div className="page" hidden={id !== page} key={id}>
            <Suspense fallback={<PageLoading page={id} workspace={workspace} />}>
              <Page workspace={workspace} />
            </Suspense>
          </div>
        );
      })}
    </>
  );
}
