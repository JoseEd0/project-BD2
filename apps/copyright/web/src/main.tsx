import "@fontsource-variable/atkinson-hyperlegible-next/wght.css";
import "@fontsource-variable/atkinson-hyperlegible-mono/wght.css";
import "./styles.css";

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
