# Local Project persistence (#124)

Local mode uses the browser's IndexedDB database `autonavlog.projects`, object store
`projects`, keyed by Project UUID. `LocalProjectRepository` in
`web/src/localProjectRepository.ts` is the storage contract: list, read, atomic write,
explicit open, and explicit delete. `LocalApplication` composes it with the existing
Python/Pyodide application. Legacy storage remains in its existing adapter; Local
storage failures never call Legacy, request authentication, or send Project data to a server.

## Durable data and session boundary

Each JSON-compatible record contains:

- `schemaVersion`, Project `id`, opaque optimistic-concurrency `token`, and `updatedAt`;
- `checkpoint`: the explicitly saved Project, or null for autosave-only Latest;
- `draft`: the latest validated working Project;
- `lastCalculation`: one last successful calculation, with its original Project,
  CalculationOutcome, destination wind, forecast metadata/selected Run, and calculation
  input fingerprint. This reuses `WorkingCalculation`'s domain validation, not the UI snapshot.

There are no UI drafts, importer bytes, owner identity, runtime handles, or raw Weather
arrays in this record. Export/backup or later Account Sync can consume these ordinary
records; this issue adds no user-facing backup or authentication UI.

The #119 session remains a separate ephemeral, per-tab recovery copy. A same-tab reload
restores it first, including invalid raw inputs and last-good results. Its optional
`durableToken` identifies the version this tab last read/wrote; it is stripped before
Python session hydration. Reload never writes the recovered copy over durable data.
A fresh navigation/tab/browser process starts blank and lists saved data. It **does not
automatically open the last Project**. The user selects a Project explicitly.

## Writes and selection

Validated edits autosave without advancing revision. Explicit Save advances revision by
one, updates the domain `Project.updated_at`, and commits the checkpoint and working draft together. Once saved, a Project keeps
its own draft and is never removed by Latest cleanup.

There is at most one usable autosave-only Latest per browser origin/profile. A successful
new Latest write replaces the former one in the same transaction. Saving Latest promotes
it to a named checkpoint. Successfully opening a different Project drops the former
Latest in that same transaction. An unavailable target or failed transaction leaves the
previous work intact. Explicit new work clears the tab session, as in #119; its durable
Latest stays recoverable until another Latest is successfully written or another Project
is explicitly opened. Closing a tab does not delete durable data.

Calculation writes use only the facade's last successful, non-blocked calculation.
Failed or blocked calculations and uncalculated edits do not clear it. An
update-and-recalculate failure still autosaves the validated working draft that the
facade committed before the calculation error. The application error carries that committed
state, including the new durable token, so the UI refreshes canonical session recovery
without replacing raw input drafts or hiding the calculation error. A same-tab reload can
then continue autosaving without conflicting with its own preceding write. Loading presents the saved last-good
snapshot and reevaluates readiness; it does not calculate or change the selected
Forecast Run. The original calculation's provenance remains separate from the edited draft.

An IndexedDB transaction's `complete` event, rather than a `put` request's success,
is the commit boundary. Checkpoint, draft, last-good, and Latest replacement are atomic.
Compare-and-swap tokens prevent a stale tab from overwriting a newer write, recreating a
deleted Project, resurrecting a replaced Latest, or deleting another tab's newer draft.
Explicit deletion uses the active working token or the version from the last displayed
selector listing, not a fresh read that would silently authorize deleting newer work. During Latest cleanup, the validated
record set is checked again inside the transaction; concurrent changes fail safely with
`PROJECT_REVISION_CONFLICT`. The user retains the tab's edits and can explicitly reopen.
There is no automatic cross-tab merge. Account namespaces add the [Account Sync contract](account_sync.md) without changing the domain record.

## Schema and corruption isolation

Database layout version 1 creates only the object store. Project payload migration is
per record, outside the database upgrade transaction, so one invalid Project cannot
block access to the store. Payload schema 2 adds an envelope update timestamp to the
schema 1 shape (which derives it from `draft.updated_at`). These are the first Local
durable formats; no previously shipped IndexedDB or Legacy server migration is implied.

`src/autonavlog/local_persistence.py` performs pure v1-to-v2 conversion followed by the
existing Pydantic Project/WorkingCalculation validation and envelope identity/revision
checks. Listing validates/migrates in memory. Explicit open persists the migrated record
only after successful validation and hydration, under the same concurrency check.
Failed validation, migration, or migration write leaves original bytes unchanged.
No database clear/delete or repository reset is used for recovery.

Unsupported/corrupt Projects are excluded from the usable selector, with a visible
warning. Healthy records remain listable, readable, and writable. Unavailable records
are also excluded from automatic Latest deletion; their original data stays available
for a future compatible version or targeted recovery. Thus an unavailable historical
record can remain on disk alongside the one usable Latest. Explicit repository deletion
can target an unavailable UUID; no manual recovery UI is introduced here.

## Quota and Weather boundary

Project data is never evicted to free capacity. On a quota error, the adapter first
clears disposable Weather caches in the exclusive `autonavlog.weather.` Cache Storage
namespace, then retries the entire transaction once. Other origin caches are untouched.
If cache release or retry fails, the operation reports `LOCAL_STORAGE_FAILED`, preserving
old Project/Last Calculation records and the user's current UI inputs. Other storage
errors fail directly. Listing failure is shown as a storage warning, so ephemeral work
can still load without pretending that durable storage succeeded.

`evictWeatherCache` is the #144 integration point. #144 owns actual Weather acquisition,
decode, raw/normalized storage choice, TTL, and invalidation; if it uses another storage
backend it must supply an equivalent disposable-cache eviction callback. This issue does
not create Weather data or duplicate Pyodide's bundled fixed fixture. Weather cache loss
never removes calculation provenance or prevents saved NAV LOG display. Full historical
Weather replay is not guaranteed.

[Platform persistence access](platform_capabilities.md) supplies the existing IndexedDB
Repository factory, the disposable-cache eviction callback, and best-effort
`navigator.storage.persist()`. Repository transaction/migration/quota semantics stay here. Unsupported, denied, and rejected
requests do not warn or block use. Actual storage operation failures do. Browser-managed
site eviction and user deletion of site data are outside the transaction guarantee;
private browsing and separate profiles/origins have separate storage lifetimes.

The implementation follows the standard
[IndexedDB transaction lifecycle](https://developer.mozilla.org/en-US/docs/Web/API/IndexedDB_API/Using_IndexedDB)
and [best-effort persistent storage API](https://developer.mozilla.org/en-US/docs/Web/API/StorageManager/persist).

## Verification

- `npm --prefix web run test:application`: adapter contract and fake-indexeddb transaction
  tests, including rollback after request success, quota retries, corruption isolation,
  concurrent Latest insertion, stale saves/opens, and storage denial.
- `pytest tests/unit/test_local_persistence.py tests/integration/test_local_calculation.py`:
  pure migration, domain corruption/identity checks, calculation/provenance and hydration.
- Build production Local assets, serve `web/dist-local` on loopback, then
  `npm --prefix web run test:local`: real IndexedDB/browser process restart, explicit
  selection, draft/checkpoint semantics, Last Calculation, Latest, quota injection,
  migration write failure, corruption isolation, and existing session/calculation regressions.

Browser tests use disposable profiles and block `/api/**`. Quota failure is injected at
the storage API boundary; it is distinguished from physically filling a device disk.
Local FORECAST now uses the [#144 Weather Adapter](local_weather.md). Its disposable
`autonavlog.weather.msm.v1` cache uses this same quota eviction boundary.

## Account context (#184)

[Authentication](authentication.md) adds per-account Repository/Session scopes. Anonymous records
remain in their original database. On sign-in, #185 validates and claims eligible anonymous
records for that account once, then imports them into its scope. Claimed originals are hidden
from anonymous and other accounts and retained so an interrupted import can resume.
Invalid records remain unclaimed and visible as unavailable in the anonymous scope.
Logout/revocation hides account copies without deleting them.
