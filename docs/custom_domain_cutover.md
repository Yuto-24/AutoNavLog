# Canonical production cutover (#123)

The canonical origin is **`https://navmate.yuto24.com`** (2026-09-27 decision in
[#123](https://github.com/Yuto-24/AutoNavLog/issues/123)). Hosting remains Cloudflare
Pages project `navmate`, using Direct Upload. Firebase provides Authentication / Sync;
the independent TAF Worker provides bounded weather retrieval. Legacy remains the
account-link/migration service until #146. Application, Calculation and Project schemas
do not change. A hostname change does not require rebuilding the same relative-path
static artifact or replacing its Firebase project/authDomain, Worker URL or Legacy URL.

## Configuration ownership and final state

These are operator settings, **not values to copy into tracked configuration**.
Keep credentials out of tracked files, build artifacts and `VITE_*` variables.

| Owner / setting | Final state |
| --- | --- |
| Cloudflare Pages / Custom domains | `navmate.yuto24.com` Active on project `navmate`; HTTPS serves the approved artifact |
| GitHub repository variable `STATIC_ORIGIN` | `https://navmate.yuto24.com` (no trailing slash) |
| Firebase Authentication / Authorized domains | `navmate.yuto24.com` authorized in the existing project; retain Legacy domain while needed |
| TAF ignored `services/taf-proxy/wrangler.local.jsonc` / `vars.ALLOWED_ORIGINS` | String `https://navmate.yuto24.com`; tracked `wrangler.jsonc` stays empty/fail closed |
| matsu Legacy environment / `AUTONAVLOG_NAVMATE_URL` | `https://navmate.yuto24.com/`; single redirect URL and derived CORS origin |
| NavMate build / `VITE_LEGACY_MIGRATION_URL` | Keep `https://navlog.yuto24.com` throughout migration |
| Cloudflare Bulk Redirect | Old `navmate.pages.dev` entry redirects to canonical after acceptance; no redirect of Legacy host |

`STATIC_APPROVED_SHA` still identifies the approved **deployed application**, not the
commit containing this runbook. The workflows already consume `vars.STATIC_ORIGIN`;
do not hardcode a domain in YAML, change the SHA for a docs merge, or shadow repository
variables with stale GitHub environment values. Historical evidence under
`docs/evidence/issue-121/` and `issue-123/` remains a record of its original date/origin.

## Ordered cutover

1. Pause recurring publication (`STATIC_AUTOMATION_ENABLED` false/unset), wait for
   in-flight publication to finish, and record deployment ID, SHA, public configuration,
   current origin/redirect/allowlist settings and rollback target. Keep automatic Pages
   Git builds/previews disabled. Follow [operations](static_operations.md) protection rules.
2. Before closing the old entry, confirm each affected user's required Project / Last
   Calculation is synced and restorable on the new origin. IndexedDB, Local storage and
   pending outbox do **not** transfer between origins. Anonymous or unsynced data needs
   resolution on the old origin first; NAV LOG JSON is not a Project backup. Do not erase
   either site's storage. A single operator account's restore is not proof for all users.
3. Verify the Pages custom domain is Active and Firebase authorizes the new hostname.
   Keep the same Firebase project, Rules and public Web configuration. Do not select
   Firebase Hosting or introduce a Pages Function for this cutover.
4. Align Legacy's single migration origin as below. Old-origin migration becomes
   unavailable at this point; temporary dual-origin TAF does not change that boundary.
   On the canonical domain, verify live Google login, migration/activation, restored
   Project and Last Calculation, and reload. Inspect a fresh browser/device too.
5. Deploy the independent TAF allowlist, test canonical CORS and destination TAF.
   A temporary CSV allowlist may include old and new origins while validating; final
   production is canonical-only. A JSON array is invalid for this Worker implementation.
6. In GitHub Settings → Secrets and variables → Actions → Variables, set `STATIC_ORIGIN`
   to the value above. Inspect the three static environments for conflicting overrides.
   Run read-only preflight/monitor at the canonical origin. An expired MSM catalog is a
   real failure requiring an approved fresh-feed publication, not a reason to bypass checks.
7. After data, Auth/Sync, Legacy, TAF and Local workflow acceptance, enable the old-host
   redirect below. Remove pages.dev from TAF's allowlist and, after old-origin recovery
   needs are resolved, Firebase Authorized domains. Verify canonical TAF still works and
   old-origin requests fail. Do not use a permanent dual-origin allowlist as completion.
8. Complete #123 publication/monitor/rollback gates on the canonical domain with current
   Free/Spark/shared-quota evidence, notification receipt, and two actual scheduled
   renewals across the previous catalog expiry. Enable recurring publication only under
   the existing explicit authorization policy. Update #123 and #116 with evidence;
   repository tests alone do not close these gates or authorize #146 shutdown.

## matsu: Legacy configuration and verification

On `matsu`, in `/srv/compose/autoNavLog`, inspect which environment file the current
service actually uses. Edit only `AUTONAVLOG_NAVMATE_URL` in that operator-owned file
(`.env`, or `.env.prod` if explicitly selected). Preserve Access Team/AUD, Firebase
settings, loopback binding, image and data volume. Keep trusted-local identity unset.
If the original startup supplied settings only as shell overrides, carry those same
settings into the operator environment first; an old container's environment is not
automatically inherited by Compose. Check the resolved config before recreation.
Do not copy a worktree `.env` or the working tree's modified Wrangler template to production.

For the default `.env` deployment, recreate the existing image after editing:

```sh
cd /srv/compose/autoNavLog
AUTONAVLOG_BIND_ADDRESS=127.0.0.1 docker compose config --format json | python3 -c '
import json, sys
s = json.load(sys.stdin)["services"]["autonavlog"]
e = s["environment"]
assert e.get("AUTONAVLOG_CLOUDFLARE_TEAM_DOMAIN") and e.get("AUTONAVLOG_CLOUDFLARE_ACCESS_AUDIENCE")
assert not e.get("AUTONAVLOG_TRUSTED_LOCAL_IDENTITY")
assert e.get("AUTONAVLOG_NAVMATE_URL") == "https://navmate.yuto24.com/"
assert all(p.get("host_ip") == "127.0.0.1" for p in s["ports"])
assert any(str(p.get("published")) == "8123" for p in s["ports"])
print("Resolved origin, Access configuration and loopback binding checked")
'
# Stop here if validation failed; also compare image/volume with the running service.
AUTONAVLOG_BIND_ADDRESS=127.0.0.1 docker compose up -d --no-build --force-recreate autonavlog
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8123/healthz
docker compose exec -T autonavlog printenv AUTONAVLOG_NAVMATE_URL
```

If production uses `.env.prod`, use `docker compose --env-file .env.prod` consistently
for all Compose commands instead, retaining the explicit loopback override. Inspect the service's existing volume mount
before and after; retain the same `autonavlog-data` volume and link registry. Never
run `down -v`. Wait for healthy, then verify the actual HTTPS endpoint, not just env text:

```sh
curl --silent --show-error --include --request OPTIONS \
  https://navlog.yuto24.com/api/navmate-migration/status \
  --header 'Origin: https://navmate.yuto24.com' \
  --header 'Access-Control-Request-Method: GET' \
  --header 'Access-Control-Request-Headers: authorization'
```

Expect 200, `Access-Control-Allow-Origin: https://navmate.yuto24.com`, allowed
Authorization header and `Vary: Origin`, without an Access login redirect. Repeat with
`Origin: https://navmate.pages.dev`: expect 403 and no allow-origin header. Neither test
proves Google token authentication; verify that in the live browser. Completed-account
Legacy HTML navigation must return the fixed 303 canonical URL, while unactivated
accounts retain Legacy use. Keep Access Bypass confined to `/api/navmate-migration/*`;
`/api/account-link`, `/api/session`, Project APIs and Legacy UI remain Access-protected.

## TAF: independent configuration and verification

Use the existing ignored local configuration and preserve account/rate namespace settings.
If none exists, copy the **clean tracked** template once per [TAF runbook](taf_proxy.md).
Set its `vars.ALLOWED_ORIGINS` to one of these JSON **string** values:

```json
"https://navmate.pages.dev,https://navmate.yuto24.com"
```

The above is transitional only. The final value is:

```json
"https://navmate.yuto24.com"
```

Do not use `["https://navmate.pages.dev", "https://navmate.yuto24.com"]`: Wrangler can
accept JSON bindings but the Worker calls `.split(",")` on this value. Local tests and
a dry-run are prerequisites, not deployment evidence:

```sh
npm --prefix services/taf-proxy ci
npm --prefix services/taf-proxy test
npm --prefix services/taf-proxy run check -- --config wrangler.local.jsonc
# Only for the separately authorized Worker publication:
npm --prefix services/taf-proxy run deploy -- --config wrangler.local.jsonc
```

Using the actual `VITE_TAF_PROXY_URL`, make `GET ...?icao=RJFM` with
`Origin: https://navmate.yuto24.com`; expect HTTP 200, matching allow-origin, JSON and
`Vary: Origin`. Follow the existing TAF cache/TTL checklist and browser display check.
After canonical-only deployment, the old Origin must return 403 without allow-origin,
including for a previously cached ICAO. Check no-Origin and unrelated Origin rejection.
TAF errors must leave calculation/storage usable; do not weaken quota or CORS controls.

## Cloudflare: close the old entry after acceptance

Use the [official Pages Bulk Redirect procedure](https://developers.cloudflare.com/pages/how-to/redirect-to-custom-domain/)
(checked 2026-09-28). Configure source `navmate.pages.dev` to target
`https://navmate.yuto24.com`, status 301, with **Preserve query string**, **Subpath
matching**, and **Preserve path suffix**. Limit this rule to the old production hostname;
leave **Include subdomains** off so deployment/preview hostnames are not silently added.
Enable the rule only after the gates above. No tracked `_redirects`, production-specific
Wrangler default, Pages Function or Firebase Hosting deployment is needed.

Verify `/` and `/release.json?cutover=1` at the old host return 301 with the expected
canonical path/query, without loops; the canonical equivalents must serve directly.
Do not redirect `navlog.yuto24.com`: its eventual retirement is #146. Browser redirects
do not transfer Local data. Retain a controlled recovery path for unsynced old-site data.

## Acceptance and recovery record

Record timestamp, deployed SHA/ID, exact settings and results: HTTPS release identity,
Auth/Sync restoration, migration preflight/redirect, TAF success and old-origin denial,
fresh same-origin MSM, FTD/FORECAST, save/reload/reopen and retained Last Calculation.
From the repository root, with the approved deployed SHA in `STATIC_APPROVED_SHA`, run:

```sh
python3 scripts/static_ops/operations.py preflight --origin https://navmate.yuto24.com --sha "$STATIC_APPROVED_SHA"
python3 scripts/static_ops/operations.py monitor --origin https://navmate.yuto24.com
```

Redirecting back to pages.dev is not success.

Artifact rollback stays on the canonical origin, with scheduler paused and in-flight
uploads completed. Keep domain/TAF/Legacy origin settings; restore the successful Pages
artifact, align approved SHA/config, verify Local retention and refresh expired weather.
Do not restore the old hostname just because the deployment predates custom-domain setup.

If an origin cutover itself must be reversed, pause publication, record any new-origin
unsynced data, and obtain separate authorization for coordinated redirect, Firebase,
TAF, Legacy single-origin and `STATIC_ORIGIN` changes. Disable the old redirect before
trying old-origin recovery (a cached 301 may also need clearing without clearing site
storage). Never delete IndexedDB/outboxes or ownership rows to make rollback appear
successful. Returning to the old origin cannot recover unsynced changes from the new one.
