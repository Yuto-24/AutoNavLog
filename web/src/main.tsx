import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "@fontsource-variable/noto-sans-jp";
import "./styles.css";
import App from "./App";

const root = document.getElementById("root");
if (!root) {
  throw new Error("AutoNavLog root element is unavailable");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
