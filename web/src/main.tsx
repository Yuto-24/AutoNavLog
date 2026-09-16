import { flushSync } from "react-dom";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "@fontsource-variable/noto-sans-jp";
import "./styles.css";
import "./flightPlanLayout.css";
import App from "./App";
import { browserPlatform } from "./browserPlatform";
import { BrowserFileInput } from "./BrowserFileInput";
import { startApplication } from "./createApplication";

const root = document.getElementById("root");
if (!root) {
  throw new Error("AutoNavLog root element is unavailable");
}

const view = createRoot(root);
let generation = 0;
startApplication(browserPlatform, ({ application, platform }) => {
  // Unmount the previous account immediately, including dialogs and pending UI work.
  flushSync(() => view.render(
    <StrictMode><App key={++generation} application={application} platform={platform} FileInput={BrowserFileInput} /></StrictMode>,
  ));
}).catch(() => view.render(
  <main className="loading-screen">
    <p role="alert">起動できませんでした。画面を再読み込みしてください。</p>
    <button onClick={() => window.location.reload()}>再読み込み</button>
  </main>,
));
