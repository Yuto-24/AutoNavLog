# Legacy account link and NavMate activation (#186)

## Authority and ownership

`UNLINKED → LINKED → MIGRATING → NAVMATE_ACTIVE` is a server-persisted state machine.
The SQLite registry in the existing data volume has unique Legacy owner, internal
account_id and Google subject keys. Only the signed Cloudflare Access owner plus a
verified, currently valid Firebase Google identity can create a link. Email matching,
client-supplied account IDs, trusted-local identity, relinking and owner merging cannot
create or change a link. Link creation does not read or copy Project data.

Firebase RS256 validation checks issuer, audience, expiry and required identity claims;
`accounts:lookup` also checks the current account, provider identity, disabled state and
revocation time. The account_id derivation is exactly [#184](authentication.md). Tokens
are used only for the current request, never persisted in the registry or Project data.

Before creating the account Application/Worker/Repository, NavMate queries activation
status with the SDK's Firebase ID token. LINKED starts migration automatically; there is
no second confirmation. Both the startup gate and Legacy use CalculationProgressOverlay
with completed/total counts. No anonymous import or existing account outbox runs before
activation is verified. An existing account's other Project IDs remain untouched.

Legacy facade operations and migration share an owner lock, including queued calculation
execution and its final persistence. A calculation already executing finishes before the
snapshot; queued work entering afterwards fails closed. Other owners remain usable.
The supported runtime is the existing single-process Uvicorn/Compose deployment; do not
add multiple workers or parallel instances sharing the Legacy data directory.

## Data and retry

The snapshot contains all visible Saved checkpoints plus their current drafts and the
owner's current autosave-only Latest. Every Project snapshot's stored owner is checked,
then `web_owner_id` is removed from draft, checkpoint and calculation snapshot. Conversion
uses LocalProjectRecord v2 and WorkingCalculation, with the same Python input fingerprint
check as Account Sync. A stale calculation is omitted; corrupt records stop activation.
UI sessions, cookies, Access credentials, weather cache and pending route imports are not
migrated. The original Legacy files remain available for #146 recovery decisions.

The Firestore REST adapter uses the user's ID token and the unchanged #185 Rules/envelope,
not admin access. BatchGet reads use a consistent read-only transaction; commits use
updateTime/exists preconditions in addition to the Rules' CAS/revision checks. Up to 25
records and approximately 6 MiB per commit bound request size; 200 Projects are a normal
multi-batch migration, not 200 sequential network writes. The final read verifies the
whole target set in one consistent snapshot before committing NAVMATE_ACTIVE locally.

An intended-write receipt is durable **before** each remote commit. Lost acknowledgements
can be recognized by exact content/version. A retry takes a fresh Legacy snapshot:

- Same ID and same durable content is adopted without another write.
- A receipt-matching remote value can be advanced to the newer Legacy value.
- An independently changed remote value, deletion or Latest device is never overwritten.
- A partial import deleted/replaced in Legacy is retired with a #185 tombstone if its
  receipt still matches; an independently changed value instead blocks activation.
- Imported Latest gets a stable migration-device ID derived from the account, so it does
  not replace an existing NavMate device's Latest. Opening/editing later uses #185 semantics.

Detected migration/storage/network errors return the account to LINKED. If the browser
closes, the server restarts, or authentication fails before the next migration request,
the durable 180-second inactivity lease releases Legacy on the next status/operation.
No unauthenticated request can release another account's lock. Completed batches survive
these failures, and the next login/retry resumes without creating new Project IDs.

NAVMATE_ACTIVE is irreversible through normal APIs. Legacy navigation returns a fixed
303 redirect; already-open Legacy pages poll and redirect, and old API editing sessions
are rejected. No NavMate changes are written back to Legacy. A device remembers completed
activation so its subsequent Local/offline use does not depend on Legacy availability.
A fresh device and a previously UNLINKED signed-in context still verify activation status
before opening during the migration period. An unavailable registry cannot prove that a
Legacy link was not created elsewhere, so that startup fails closed with Retry/Sign out.
Anonymous Local work remains available after sign-out. Do not cache negative link status. Runtime
shutdown and eventual removal of the activation service dependency remain #146.

## Configure the migration period

Use the same Firebase project as NavMate Authentication/Account Sync. Deploy the existing
Firestore Rules as described in [account_sync.md](account_sync.md). No Rules change,
service-account key, Cloud Function or paid plan is required for this migration.

Legacy Compose environment (public Firebase Web config, not admin secrets):

```dotenv
AUTONAVLOG_FIREBASE_PROJECT_ID=your-project
AUTONAVLOG_FIREBASE_API_KEY=your-public-web-api-key
AUTONAVLOG_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
AUTONAVLOG_FIREBASE_APP_ID=your-web-app-id
AUTONAVLOG_NAVMATE_URL=https://your-navmate-host/
```

Keep the existing Cloudflare Team domain/AUD and loopback binding. Add the Legacy host
to Firebase Authentication authorized domains. The Account button in Legacy starts the
link; the user chooses Google explicitly. Popup preparation occurs before the link click
so the click retains browser user activation. Default firebaseapp.com auth hosting is
supported by the Legacy CSP.

In the NavMate Local build, alongside its existing Firebase configuration:

```dotenv
VITE_LEGACY_MIGRATION_URL=https://your-legacy-host
```

Cloudflare Access normally protects the whole Legacy host. Configure a **more specific
Bypass application only for `/api/navmate-migration/*`** so a Google-authenticated NavMate
browser can reach activation without a cross-site Access cookie. These routes validate
Firebase tokens themselves, resolve the stored link internally, and allow CORS only from
the configured NavMate origin. Never bypass `/api/account-link`, `/api/session`, any
Project route, or the Legacy UI. The link POST requires signed Access and the Legacy
HTTPS Origin/Host match. Keep `AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` unset on exposed services.
CORS preflight is credential-free; bearer tokens never appear in URLs.

If migration is not configured, Legacy retains normal storage behavior and the Account
dialog explains that linking is unavailable. During rollout, enable both sides together;
do not advertise a NavMate build without its activation gate to linked users. Keep registry
and Project data in the same preserved volume. Do not manually edit ownership rows.

## Verification

Automated backend tests cover signed credentials, one-to-one/idempotent registration,
owner isolation, real Legacy calculation conversion, all four states, update locking,
200 Projects, union, lost acknowledgements, source updates/deletes, remote-ahead failure,
Latest ownership mismatch, final verification failure, lease recovery and redirects.

The browser fixture uses real Legacy storage/facade/calculation, migration APIs, Firestore
REST, deployed emulator Rules, Firebase browser SDK and account IndexedDB. Only Google
identity exchange is synthetic; signature validation is independently tested with RSA.
It verifies progress before Application creation, failure/retry and fresh-device restoration.
This is not evidence of live Google popup consent, production Access path policy or a
physical iPad/Safari acceptance run.

Reproducible local commands (Python 3.12, Node 22+, Java 21; use Docker for application runtime):

```sh
pytest tests/integration/test_legacy_migration.py tests/unit/test_migration_remote.py
npm --prefix web run test:application
AUTONAVLOG_TEST_FIXTURES=1 npm --prefix web run prepare:local
# Keep these test-only processes on loopback; never expose the synthetic auth fixture.
PYTHONPATH=src:tests/integration python -m uvicorn migration_harness:app --host 127.0.0.1 --port 8186
firebase emulators:start --only firestore --project demo-autonavlog-sync
npm --prefix web run test:migration
```

On the actual configured hosts, verify link-only leaves Legacy edit/save/calculation
working; first NavMate login shows progress and rejects a simultaneous Legacy edit;
a connection failure leaves data intact and releases Legacy (within the lease if the
client disappears); retry preserves IDs; fresh-device login restores Saved/Latest and
NAV LOG; successful Legacy navigation automatically redirects. Verify a different Google
account and a different Access owner cannot link to or retrieve the first owner's data.
Record live deployment/physical acceptance separately from emulator and browser results.
