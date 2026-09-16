// Test-only entry: real SDK, real Application/UI/IndexedDB, fake Google backend.
// This entry and its credential injection are never included in the production build.
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { initializeFirestore, connectFirestoreEmulator } from "firebase/firestore";
import { createFirestoreSyncRepository } from "../../src/firestoreSyncRepository";
import { getApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, signInWithCredential } from "firebase/auth";
import { createFirebaseAuthProvider } from "../../src/firebaseAuthProvider";
import { observeAccountContexts } from "../../src/accountContext";
import { browserPlatform } from "../../src/browserPlatform";
import { BrowserFileInput } from "../../src/BrowserFileInput";
import App from "../../src/App";
import "leaflet/dist/leaflet.css";
import "../../src/styles.css";
import "../../src/flightPlanLayout.css";

const syncEnabled = new URLSearchParams(location.search).has("sync");
const provider = await createFirebaseAuthProvider({ apiKey: "issue-184-test", projectId: syncEnabled ? "demo-autonavlog-sync" : "autonavlog-test", authDomain: "autonavlog-test.firebaseapp.com", appId: "test-app" });
// Playwright WebKit's routed HTTP emulator stream can remain buffered. Complete
// each response with the SDK transport option; live builds retain SDK defaults.
if (syncEnabled) connectFirestoreEmulator(initializeFirestore(getApp(), {
  experimentalForceLongPolling: true,
}), "127.0.0.1", 8088);
const root = createRoot(document.getElementById("root")!);
let generation = 0;
const contexts: any[] = [];
observeAccountContexts(browserPlatform, provider, ({ application, platform }) => {
  contexts.push(application);
  flushSync(() => root.render(<App key={++generation} application={application} platform={platform} FileInput={BrowserFileInput} />));
}, syncEnabled ? createFirestoreSyncRepository : undefined);
(window as any).authTest = {
  state: provider.getState,
  contexts,
  generation: () => generation,
  signIn: (subject: string) => signInWithCredential(getAuth(), GoogleAuthProvider.credential(subject)),
  signOut: () => provider.signOut(),
  refresh: () => provider.refresh(),
};
