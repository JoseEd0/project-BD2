import "@fontsource-variable/bricolage-grotesque/opsz.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html no tiene el elemento #root");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
