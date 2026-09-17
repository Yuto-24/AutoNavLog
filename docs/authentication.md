# Local Account authentication (#184)

Local mode supports Firebase Authentication + Google Sign-In through `AuthProvider`.
Anonymous Local-only use does not require Firebase configuration, an account, or a server.
Remote Project/Last Calculation synchronization and anonymous-data import are described in [Account Sync](account_sync.md);
Cloudflare Access owner migration belongs to #186. Legacy mode retains its existing identity.

## Configure Google Sign-In

1. Create a Firebase project on the free Spark plan and register a Web app. No Firestore,
   Cloud Functions, billing upgrade, allowlist, or invitations are required for this feature.
2. In Authentication → Sign-in method, enable Google and set the support email. Enable
   only Google; password authentication and provider linking are not implemented.
3. Add each UI host to Authentication → Settings → Authorized domains. Add `localhost`
   explicitly for local testing when it is absent. Serve the deployed UI over HTTPS.
4. Put the Web app's **public** config in ignored `web/.env.local`, or provide the same
   build variables in the static build environment:

   ```dotenv
   VITE_FIREBASE_API_KEY=your-web-api-key
   VITE_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
   VITE_FIREBASE_PROJECT_ID=your-project
   VITE_FIREBASE_APP_ID=your-web-app-id
   ```

   These identify the Web app; never put a service-account private key or admin credential
   in a `VITE_` variable. Configure the API key and authorized domains for this Web app.
5. Build/serve Local mode using [the existing Local instructions](local_calculation_poc.md#起動と切替).
   Build-time settings require a rebuild. The standard Compose Legacy UI does not use them.
6. Open the header's 「アカウント」 button, then 「Googleでログイン」. Sign-in uses the SDK's
   popup flow; if blocked, permit popups for this site and retry. Close/cancel preserves
   the current Local work. An unconfigured build explains that sign-in is unavailable.

A Firebase project was not available during implementation. Automated verification uses
real Firebase SDK credential exchanges with an intercepted test backend; it does **not**
prove live Google consent, authorized-domain configuration, or physical Safari acceptance.
After provisioning, verify login, browser restart, logout, and reauthentication on the
actual PC/iPad deployment. No remote deployment is performed by this change.

## Identity and ownership

`AuthProvider` exposes only `{ account_id, displayName }` and account state/operations.
Firebase API/types remain inside its adapter. Application and Project do not receive
Firebase UID, email, tokens, or Firestore paths.

The initial mapping is deterministic, versioned, and independent of the device and Firebase
UID: `account_v1_` followed by the lowercase SHA-256 hex digest of the UTF-8 JSON array
`["autonavlog.account.v1","https://accounts.google.com",googleSubject]`.
`googleSubject` comes from the authenticated SDK user's single `google.com` provider identity.
Email/display-name changes do not change ownership. No random local account is minted on
another device; no mapping server or provider-linking framework is required. Treat this
mapping as an identity contract, not a replaceable display hash. A future provider change
must explicitly preserve this account_id mapping. The ID is not an authentication credential;
#185 must enforce remote ownership against verified authentication, never trust a client-supplied ID.

Anonymous data continues to use `autonavlog.projects`. Each account uses
`autonavlog.projects.<account_id>` with the unchanged #124 records and transactions. The
namespace expresses ownership without changing the Python Project schema. All list/read/
write/open/delete and Latest cleanup operate inside that namespace. A Project ID/token
from another namespace does not grant access. Each namespace keeps its own Latest; no
cross-account cleanup occurs. With Account Sync enabled, anonymous data is claimed once by the login account; cross-device Latest follows [Account Sync](account_sync.md).

Each authentication lifetime gets a new Application/Worker and Repository handle. Closing
that context rejects queued work, late responses and subsequent storage operations, and
aborts its still-active IndexedDB transactions. A provider subject change closes the old
context before asynchronous account_id derivation starts. A save
already committed before closure remains in its original account; it cannot be retargeted.
The UI is unmounted on a boundary change so raw form values, dialogs and Last Calculation
cannot survive in another account's view.

Session keys use the same account scope. Only initial same-tab reload can restore that
scope's ephemeral session. Logout/switch clears the outgoing ephemeral session, never its
Repository. Reauthentication and fresh tabs require explicit selection from saved Projects.
These are application visibility boundaries, not encryption against someone controlling the
browser profile or developer tools.

## Persistence, offline and revocation

The SDK's `browserLocalPersistence` owns credential persistence and cross-tab auth changes.
No application token cache is added. When the UI/runtime assets are available, cached
Firebase identity can initialize without the auth backend and open that account's Local
copies. Network/provider failures during refresh preserve the account and Local work.
This does not add an offline application shell or Service Worker (#187); uncached UI/Pyodide
assets still require their existing delivery path.

Firebase 12.19.0's startup `reloadAndSetCurrentUserOrClear` clears a cached user for errors
other than `auth/network-request-failed`. A narrow fetch guard exists **only during SDK
initialization**, only for `identitytoolkit.googleapis.com` / `securetoken.googleapis.com`,
and only for HTTP 429/5xx or explicit transient 400 codes (`TOO_MANY_ATTEMPTS_TRY_LATER`,
`QUOTA_EXCEEDED`, `INTERNAL_ERROR`). It makes those transient responses network failures
and restores the original fetch afterwards. Terminal 400 responses remain unchanged.
This avoids a second credential store or custom refresh implementation. Reassess the guard
when upgrading the pinned SDK, with the startup outage regression tests.

The provider requests a fresh ID token on reconnect/focus and every 60 seconds. Explicit
`auth/user-disabled`, `auth/user-token-expired`, `auth/invalid-user-token` and
`auth/user-not-found` responses close access; SDK sign-out propagates to other tabs. Offline
clients cannot discover remote revocation until connectivity returns. Google consent removal
is not a signal Firebase necessarily delivers immediately; this adapter closes access when
Firebase confirms credential invalidation. It does not claim to poll Google's permission page.

Explicit logout closes the context immediately and asks the SDK to persist sign-out. If
persistence fails, a provider-level notice in the new view explains that a reload may
restore login and offers a logout retry; access remains closed meanwhile. Local
copies remain. The same Google identity reopens the same namespace; anonymous/other accounts
cannot access it through Application. Site-data deletion remains outside persistence guarantees.

## Verification

- `npm --prefix web run test:application`: identity stability, every Repository operation,
  namespace isolation, queued saves, transaction closure and Session scope.
- `npm --prefix web run test:auth`: real SDK + browser IndexedDB + Local Application/UI,
  intercepted auth backend; persisted login, outages at startup/refresh, revocation,
  logout, cross-tab closure, account switching, reauthentication and responsive flow.
- `npm --prefix web run test:local`: existing anonymous persistence/session/calculation regressions.
- Legacy and Local builds/typecheck; release checks; isolated Compose health check.

Official references: [Google sign-in](https://firebase.google.com/docs/auth/web/google-signin),
[auth persistence](https://firebase.google.com/docs/auth/web/auth-state-persistence),
[session revocation](https://firebase.google.com/docs/auth/admin/manage-sessions),
[Auth SDK](https://firebase.google.com/docs/reference/js/auth).
