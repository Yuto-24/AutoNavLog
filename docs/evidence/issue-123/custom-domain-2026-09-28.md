# Custom-domain repository handoff (2026-09-28)

Scope: #123 canonical-origin contract and regression coverage. This is not a production
deployment or completed automation acceptance. [Cutover runbook](../../custom_domain_cutover.md)
contains the ordered operator actions. No Cloudflare/Firebase/GitHub settings, production
environment files, Worker deployment or scheduler enable switches were changed.

## Read-only production observations

Checked with curl without following redirects at approximately 10:34–10:37 UTC:

| Probe | Result |
| --- | --- |
| `https://navmate.yuto24.com/release.json` | HTTP 200, clean v1.20.1, SHA `a4de2a226dfa205b8be42fb62a710a1b06271ed8` |
| `https://navmate.pages.dev/` | HTTP 200; old entry is still served, not redirected |
| Legacy `/api/navmate-migration/status`, OPTIONS with canonical Origin | HTTP 200, matching allow-origin, Authorization / Content-Type, GET / POST / OPTIONS, `Vary: Origin` |
| Same Legacy preflight with pages.dev Origin | HTTP 403, no allow-origin |
| Existing production TAF endpoint, GET `?icao=RJFM` with canonical Origin | HTTP 403, no allow-origin |
| Same TAF GET with pages.dev Origin | HTTP 200, allow-origin is pages.dev |
| Canonical `/weather/msm/catalog.json` | Generated `2026-09-24T11:53:06.314558Z`, expired `2026-09-24T17:53:06.314558Z` |

The Legacy result verifies the public CORS boundary only; it does not prove the selected
host env file, live Google identity, completed-account redirect or data restoration.
The Auth/Sync/Last Calculation acceptance reported by the operator on September 27 is
in [#123](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5852546355), not
a new browser acceptance run here. GitHub variable values and current provider plans /
quota were not verified in this task. An initial Python urllib probe received uniform
403 responses, including for the release file; the curl observations above supersede
that inconclusive probe and must not be confused with it.

## Remaining operator gates

- **matsu:** confirm the active environment file and container's canonical NavMate URL,
  loopback/Access and preserved volume. Public CORS already matches the decision;
  recreate only if configuration needs changing. Verify completed-account 303 and
  fresh-browser migration/Sync according to the runbook.
- **TAF / Cloudflare:** deploy the ignored CSV-string allowlist; canonical TAF currently
  fails. Complete real TAF/cache acceptance. If both origins are temporarily permitted,
  remove pages.dev after old-entry acceptance and verify old-origin denial.
- **Cloudflare / Firebase:** confirm existing custom domain and Authorized domains;
  resolve unsynced old-origin data, then activate the old-host Bulk Redirect and retire
  the old Auth domain when recovery needs allow. Leave Legacy hostname in place for #146.
- **GitHub Dashboard:** set/verify canonical `STATIC_ORIGIN` and absence of environment
  overrides; keep approved deployed SHA distinct from this docs/test commit. Verify
  protections, Pages-only token and current quota evidence. The expired weather requires
  an approved fresh-feed publication, not a freshness exception.
- Complete canonical monitor/publication, notification receipt, two scheduled renewals
  crossing the prior catalog expiry, and paused-scheduler same-origin rollback with
  Local retention. #123 and #116 automation checkboxes / #146 dependency remain open.

## Repository boundaries

The checked-in Worker allowlist stays the empty string. Compose and Pages configuration,
workflow variable injection, runtime code, Application / Calculation / Project schemas,
VERSION and CHANGELOG are unchanged. Existing operator changes in the primary checkout
were preserved, including its incompatible array-valued Wrangler edit and `.env.prod`.
The corrected production string belongs in ignored `wrangler.local.jsonc`, not that
tracked template. This PR is `Release: not-required` (operations docs and tests only).

Independent review by GPT-6 Sol identified a missing explicit loopback override in the
initial recreation instructions. The runbook now validates resolved Access/origin/bind
settings and explicitly binds recreation to loopback. Re-review found no remaining
actionable issues. No UI/browser automation is required for these docs/test-only changes
under AGENTS.md; live browser acceptance remains an operator gate.
