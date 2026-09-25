// Test-only entry: real SDK, real Application/UI/IndexedDB, fake Google backend.
// This entry and its credential injection are never included in the production build.
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { initializeFirestore, connectFirestoreEmulator } from "firebase/firestore";
import { createFirestoreSyncRepository } from "../../src/firestoreSyncRepository";
import { getApp, initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, signInWithCredential } from "firebase/auth";
import { createFirebaseAuthProvider, googleAccount } from "../../src/firebaseAuthProvider";
import { observeAccountContexts } from "../../src/accountContext";
import { browserPlatform } from "../../src/browserPlatform";
import { BrowserFileInput } from "../../src/BrowserFileInput";
import App from "../../src/App";
import { activateLegacyAccount, type MigrationProgress } from "../../src/legacyMigration";
import { CalculationProgressOverlay } from "../../src/components/CalculationProgressOverlay";
import "leaflet/dist/leaflet.css";
import "../../src/styles.css";
import "../../src/flightPlanLayout.css";

const syncEnabled = new URLSearchParams(location.search).has("sync");
const config = { apiKey: "issue-184-test", projectId: syncEnabled ? "demo-autonavlog-sync" : "autonavlog-test", authDomain: "autonavlog-test.firebaseapp.com", appId: "test-app" };
// Playwright WebKit's routed HTTP emulator stream can remain buffered. Complete
// each response with the SDK transport option; live builds retain SDK defaults.
if (syncEnabled) connectFirestoreEmulator(initializeFirestore(initializeApp(config), {
  experimentalForceLongPolling: true,
}), "127.0.0.1", 8088);
const provider = await createFirebaseAuthProvider(config,
  syncEnabled ? undefined : async user => ({ accountId: (await googleAccount(user)).account_id, deleting: false }));
const root = createRoot(document.getElementById("root")!);
let generation = 0;
const contexts: any[] = [];
const migrationEnabled = new URLSearchParams(location.search).has("migration");
function progress(value: MigrationProgress) {
  flushSync(() => root.render(value.error
    ? <main><p role="alert">{value.error}</p><button onClick={value.retry}>再試行</button></main>
    : <CalculationProgressOverlay title="NavMateへ引継ぎ中" percent={value.percent} message={value.message} />));
}
observeAccountContexts(browserPlatform, provider, ({ application, platform }) => {
  contexts.push(application);
  flushSync(() => root.render(<App key={++generation} application={application} platform={platform} FileInput={BrowserFileInput} />));
}, syncEnabled ? createFirestoreSyncRepository : undefined,
  migrationEnabled ? (id, signal) => activateLegacyAccount(id, signal, progress) : undefined,
  (error, retry) => progress({ percent: 0, message: "Legacyの引継ぎ状態を確認中", error, retry }));
(window as any).authTest = {
  state: provider.getState,
  contexts,
  generation: () => generation,
  signIn: (subject: string) => signInWithCredential(getAuth(), GoogleAuthProvider.credential(subject)),
  register: (subject: string, cachedOldId?: string) => provider.signInWith(async () => {
    const user = (await signInWithCredential(getAuth(), GoogleAuthProvider.credential(subject))).user;
    if (cachedOldId) localStorage.setItem(`autonavlog.account.current.${subject}`, cachedOldId);
    return user;
  }),
  deleteAccount: () => provider.deleteAccount(),
  signOut: () => provider.signOut(),
  refresh: () => provider.refresh(),
};
