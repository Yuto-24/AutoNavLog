# Platform Capability boundary (#120)

`web/src/platform.ts` is the runtime contract. `main.tsx` supplies `browserPlatform`
and `BrowserFileInput` to the existing application/React composition. There is no
platform detection in feature components, DI container, registry, Native adapter,
or PWA requirement. A future shell supplies the same capabilities and its file-open
control at this composition point. Domain and Calculation Core do not import this
contract; Python/Pyodide still uses the existing Importer and CalculationService.

| Responsibility | Owner |
| --- | --- |
| File content acquisition, download, Clipboard, external URL, connectivity hint | Browser Platform |
| File picker/drop DOM events and file selection cancellation | `BrowserFileInput`, supplied by the composition root |
| KML/KMZ parsing, archive limits, document selection and route candidates | Existing `import_kml_or_kmz` / `import_kml_text` |
| Operations, working state, draft commit, calculation and error meaning | #118 Application |
| Reload-only adoption, snapshot validation, raw edits, recovery failure policy | #119 Application Session |
| Project / Latest / checkpoint / revision / Last Calculation, migration, transactions and conflict/quota policy | #124 Local Project Repository |
| Browser storage handles and creation of the existing IndexedDB Repository | Platform persistence access |
| Information read markers and fallback to in-memory markers | Information feature, using injected key/value storage |

## Files and Importer

Picker and drop both provide a content source to `files.read`. The result is
`PlatformFile { name, mediaType, content: Uint8Array }`; no filesystem path crosses
into Application or Importer. `fileInput.ts` only serializes bytes into the existing
`ImportRouteInput` contract (`filename` plus `content_base64`, or pasted `kml_text`,
and optional `kmz_kml_filename`). Filename identifies the format/source and the
selected KMZ member identifies a document inside the archive, never a local path.

The Browser rejects oversized selected files before reading them. The shared Python
Importer remains authoritative for archive, expanded-content, XML, coordinate and
route limits. Local and Legacy both call it; Local now supports compressed KMZ and
returns `KMZ_DOCUMENT_SELECTION_REQUIRED` with the same `details.candidates` as
Legacy. Pending bytes and document selection still belong to #119 reload recovery.
Picker cancellation is a no-op, reselecting the same file remains possible, and a
failed read/import preserves the previous candidates. No ZIP/XML parser was added.

## Result export and Clipboard

The NAV LOG actions serialize the displayed `CalculationOutcome` and destination
wind into UTF-8 JSON (`format: autonavlog.navlog`, `version: 1`). Edited Project
inputs are deliberately excluded: they may no longer describe that calculation.
This is a result document, not a Project backup or a second Repository schema.
It does not trigger calculation, checkpoint, revision or durable Project writes.
The existing Session lifecycle may still save its ephemeral working snapshot.

Browser save uses a Blob URL and the standard anchor download attribute; it cleans
up the element immediately and revokes the URL after the browser can consume it.
Success means the download was initiated, not that the user accepted a save dialog
or the OS durably wrote a file. Clipboard write copies the same JSON text.

Clipboard read/write are invoked directly within the user's click activation,
without an earlier async permission probe. Unsupported and denied access produce
`PlatformError` with capability and `UNSUPPORTED` / `FAILED` codes. Read failure
opens the existing manual KML paste dialog; write failure is visible and never
reports a successful copy. Clipboard generally requires a secure context and
Browser permission; file input/download use standard Browser controls.

External reference links use a shared link component and `openExternalUrl`, allow
only HTTP(S), and open with `noopener noreferrer`. Standard anchor affordances
(modifier clicks, context menus and URL copying) are retained. Leaflet's map
attribution remains Leaflet-owned native link markup.

## Storage and network

Platform supplies the sessionStorage-backed key/value handle, the localStorage
handle for existing Information markers, best-effort retention, and a factory for
the **existing** `IndexedDbProjectRepository`. `LocalApplication` receives only the
Repository factory. IndexedDB transaction mechanics stay inside that adapter.
Platform never lists Projects, manages Latest, advances revisions, validates
records, migrates schemas or decides transaction/quota retry policy.

Only #119 decides whether to adopt a session (same-tab reload), what key to remove,
and how to report failed recovery. Only #124 decides when to evict the disposable
Weather cache and retry a transaction. `browserStorage.ts` implements that existing
dedicated cache eviction callback; #144 still owns actual Weather cache data/TTL.
Unavailable storage does not trigger a Legacy fallback or delete Project data.

`networkAvailability()` is the requested minimal `online` / `offline` / `unknown`
hint from Browser connectivity, not a service health probe. It does not gate Local
import, calculation, storage or export, and does not report weather acquisition
success. Feature-specific fetch/error handling remains with its adapter. No
background polling, connectivity UI, share sheet or preference framework is added.

## Verification

- `npm --prefix web run test:application`: capability errors, byte preservation,
  injected Session/Information storage, existing Repository transactions, and
  architecture dependency checks.
- `platform.spec.ts` runs in both Legacy and production Local Browser suites:
  compressed KMZ picker, KML drop, cancellation/failure preservation, Clipboard
  activation and manual fallback, actual download/copy and denial, external links,
  and workflow positions at 1100/1440 px. Local blocks `/api/**` and also exercises
  calculation/export offline after assets have loaded.
- `session.spec.ts` covers KMZ selection/reload in both runtimes, with no Local skip.
- Existing Local calculation/IndexedDB and Legacy Clipboard/Session regressions
  remain in use. Python integration compares KMZ candidates with plain KML and
  checks explicit selection errors; existing Importer security tests retain limits.

These automated checks cover Chromium. They do not claim physical iPad/Safari,
Native shell, PWA installation, offline first-load, or production Static hosting
acceptance; those remain separate runtime/roadmap work.
