import "@fontsource-variable/archivo/wdth.css";
import "@fontsource-variable/source-serif-4/opsz.css";
import "./styles.css";
import "./reader/textLayer.css";

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
