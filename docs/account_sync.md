# Account Sync (#185)

Local mode composes `AccountProjectRepository` with `AccountSyncController` and
`AccountSyncRepository`. The initial remote adapter uses the existing Firebase SDK's
Firestore transactions and snapshots. Domain Project / WorkingCalculation schemas and
UI have no Firebase types, document paths, or server timestamp fields.

## Setup

Enable Google authentication as described in [authentication.md](authentication.md).
Create the default Cloud Firestore database in the same Firebase project, in production
mode, then deploy this repository's `firebase/firestore.rules` and
`firebase/firestore.indexes.json` with the Firebase CLI:

```sh
firebase deploy --only firestore:rules,firestore:indexes --project YOUR_FIREBASE_PROJECT_ID
```

Deployment changes the remote project's security policy and requires explicit approval.
Do not use test-mode public rules or an admin/service-account credential in the browser.
The configured Local build automatically enables Account Sync after login; the standard
Compose Legacy UI retains its existing server persistence. No remote deployment is
performed by the implementation PR.

Rules compare the path's Google subject with the authenticated token's Google identity,
not with a client-supplied account_id, email, display name, or arbitrary Firebase UID.
Unknown paths and physical tombstone deletion are denied. Payloads are JSON strings so
ordinary Project JSON, including nested arrays, remains independent of Firestore types.
The adapter's document-size/quota failures preserve the Local outbox and show their
impact on cross-device continuation; no Local Project is evicted or switched to Legacy.

## Durable contract

The #124 domain record remains checkpoint + draft + Last Calculation. The account
IndexedDB namespace adds separate sync metadata: an opaque CAS version, acknowledged
base, pending group, in-flight operation, conflict and deletion state. The Rules-enforced monotonic sync revision rejects delayed snapshots older than an
acknowledged save. UUID operation identities support CAS and idempotent replay. Explicit
Save revision remains independent of both. Autosave commits to IndexedDB
before any network operation; a failed network write cannot roll back a Local save.

A remote transaction writes every member of an atomic group, or none. Retries use the
same operation identity after a lost acknowledgement. Local edits during transmission
stay dirty against the acknowledged base. Initial download and snapshot invalidations
union all synced Project bodies and Last Calculations into Local storage. Idle retry
ticks do not repeatedly download the entire account. The SDK's cache or optimistic
snapshot is never proof of a committed server write. Synced data can then be listed and
opened offline; this does not supply an offline application shell or cache every runtime
asset (#187).

Anonymous records are durably claimed once by the login account and remain as recovery
originals in the anonymous store, hidden from anonymous use and other accounts. Claim ownership
is part of the anonymous transaction snapshot: a stale Latest replacement or Project open
conflicts if another tab claims a candidate before commit. Latest cleanup also excludes
claimed rows inside the write transaction, preserving originals for interrupted import recovery. Named
Projects enter the account union immediately. Anonymous Latest is staged in the account
Local Repository immediately and remains readable/editable offline. Its upload waits for
the first successful remote union so an existing cloud Latest for this device cannot be overwritten.
If Latest collides, the account Latest remains and a blocking modal asks for a named save
or discard of the claimed Local Latest. Closing/reloading before choosing preserves it.
A failed initial remote connection retains the staged working copy and claimed originals and retries automatically.

A browser origin/profile has one device identity. Account Latest belongs to the last
editing device; merely reading it does not transfer ownership. Opening another Project
or replacing that device's Latest creates a durable tombstone for the discarded Latest.
Other devices' Latest and all explicit checkpoints remain. A conflict copy is a normal
saved Project with a new UUID and independent revision, never a second Latest.

## Conflict and recovery

The first stale edit is retained locally, then additional edits are blocked until an
explicit Local / Remote / Both choice. No silent last-write-wins or field merge occurs.
Both retains the remote UUID and copies the Local branch to a new UUID. Project,
revision, calculation selection and copy are one IndexedDB transaction and one remote
transaction. A repeated conflict retries the same unpublished copy instead of publishing
extra copies. A committed copy keeps its own identity.

Each chosen Last Calculation is rechecked using the existing Python input fingerprint.
A stale result is omitted, never attached to a different Project state. Copy reidentifies
the matching Project/outcome without changing its calculation provenance. Session
recovery retains an opaque repository baseline (checkpoint and CAS base) so a reload
cannot replace an old working checkpoint with a newer remote one before detecting the
first edit's conflict. This baseline is stripped before Python hydration and never synced.

## Delete and Undo

Deletion atomically stores PENDING_DELETE plus its deadline and hides the Project
immediately. A snackbar offers Undo for approximately ten seconds. The pending state and
Undo deadline sync to other devices. Undo creates another CAS operation restoring ACTIVE.

At the saved deadline, PENDING_DELETE is **effectively DELETED**, even when no browser is
running. Firestore Rules use request time to reject resurrection of an expired pending
record; correctness does not depend on unload or a server timer. The next client sync
normalizes the stored label to DELETED. This is a durable tombstone, not physical deletion
or a user-facing Trash. An offline deletion keeps its original deadline until uploaded.
A stale Local branch can be preserved as a new UUID, but cannot revive a deleted UUID.

## Verification

```sh
npm --prefix web run test:application
AUTONAVLOG_TEST_FIXTURES=1 npm --prefix web run prepare:local
npm --prefix web exec -- playwright install chromium webkit
npx --yes firebase-tools@15.10.1 emulators:exec --only firestore --project demo-autonavlog-sync \
  "npm --prefix web run test:firestore && npm --prefix web run test:sync"
```

Use Java 21 and the repository's supported Python/Node environment. Emulator tests use
the real Firebase SDK with synthetic authenticated Google identities; no live account
or production data is involved. Browser tests cover Chromium and iPhone/iPad WebKit
profiles. The routed HTTP emulator harness forces the SDK's
[long-polling transport](https://firebase.google.com/docs/reference/js/firestore.firestoresettings#firestoresettings_experimentalforcelongpolling)
because WebKit can buffer its streaming response indefinitely. Production uses the SDK
defaults; emulator success does not verify that transport on physical Safari. Windows
Edge is also exercised separately. These profiles do not constitute physical Safari
acceptance.

Physical acceptance must use the configured Local build and deployed Rules: create and
calculate on Windows Chromium, open on iPhone/iPad Safari with the same account, edit
offline and reconnect, resolve each conflict choice, test delete/Undo/expiry, restart
after sync, and check another account cannot see those Projects. Keep #185, #183 and
#116 device acceptance gates open until this is recorded. Cloudflare owner linking and first-use activation are documented in
[Legacy migration](legacy_migration.md).
