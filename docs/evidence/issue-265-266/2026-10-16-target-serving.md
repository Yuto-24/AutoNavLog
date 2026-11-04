# Pages target consistency and canonical serving investigation

Scope: #265 / #266, following Draft PR #267 at
`1f04cd3adc406df6d21ba8cf1eeb56d03af7f969`. No merge, provider writes, app
publication, Rules changes, scheduler/workflow changes or new credentials.

## #265: three project reads must agree

`Operator.target()` previously checked source/safety and computed its fingerprint
from the first project read A, while `pages_target.inspect()` independently compared
its two reads B/B around the complete deployment inventory. A/B/B could therefore
pass even when B enabled Git builds or changed runtime bindings.

The caller now passes its initial project as `expected_project`. The inventory
observer requires its before/after reads and this initial read to agree. Comparison
uses canonical JSON in memory, so boolean false and numeric zero are distinct; no
raw project configuration is returned or logged. Existing pagination, unknown/busy
rejection, Functions, source, approved SHA, Rules and fingerprint gates remain.

The actual target()/inventory path is tested through synthetic HTTP responses for
A/A/A, A/B/B, A/A/B and A/B/A. Changes include enabled production builds, two allowed
source/configuration transitions, false-to-zero type drift, and runtime bindings.
The new 20 cases against the original #267 functions reproduce 7 failures / 13
passes; the corrected implementation passes all 20. This is observed-read
consistency, not a new lock or a guarantee against changes after the final GET.

## #266: current GET-only evidence

At 2026-10-16 07:42:57 UTC (16:42:57 JST), the existing operations fetch obtained
canonical `/release.json`: HTTP 200, clean v1.20.1,
`a4de2a226dfa205b8be42fb62a710a1b06271ed8`.
The received release bytes have SHA-256
`54147dd5af57ce47a903e478d449d7c91ad1e15668ad1bc759e726effc07e946`.
They were used as the actual `verify_serving()` input; expected values were not
copied from an earlier run. Public release bytes were held in a temporary private
local directory and discarded afterwards.

- Actual unmodified `verify_serving()`: root HTTP 403; manifest and missing asset
  checks **not executed**, because the root failure stops the method.
- Root error response: `Server: cloudflare`, `Content-Type: text/plain;
  charset=UTF-8`, no `cf-mitigated`, no ETag; 17-byte error body SHA-256
  `2938e9f1284180959e33ab1718d0793a72ff6e4cdb8108c34dcd14e69446de5c`.
  Request Ray ID: `a4b56e13eaa3b34a-KIX`.
- A bounded subsequent default-client GET extracted **error code 1010** from the
  response (Ray ID `a4b56f206889d427-KIX`). This is evidence beyond a User-Agent
  comparison: Cloudflare documents 1010 as a client/browser-signature access block.
  It does not identify the precise configured rule or prove that a particular
  alternative identity is authorized by the owner's policy.
- Existing `operations.fetch()` to the identical root with its established honest
  identifier `AutoNavLog-StaticOperations/1.0`: HTTP 200, HTML, no-cache, 1022 bytes,
  SHA-256 `44e6e333e078407e1ee5f582d6fb0905fb2fda0fddfc41e0343001449135353f`.
  **ETag absent**. Its successful byte fetch is not a successful serving/header
  gate. Ray ID `a4b56e14897a963a-KIX`.
- No challenge cookies, alternate origin, query/cache bust, browser impersonation
  or access-control change was used. Absence of `cf-mitigated` does not establish
  unrestricted access. Raw headers/cookies/credentials were not retained.

The existing local Wrangler OAuth entry is expired and does not list a WAF-read
scope; no login, token refresh, permission request or security-settings mutation
was attempted. The site owner subsequently explicitly confirmed in this task that
GETs using the existing `AutoNavLog-StaticOperations/1.0` identity are permitted.
HTTP 200 from the existing fetch was not substituted for that confirmation.

The minimal #266 code change extracts `static_request()` from the existing fetch
and uses the same honest identifier for HTML, manifest and missing-asset checks.
`verify_serving()` still accesses response headers directly; it is not replaced by
the byte-only `fetch()`. Status, MIME, ETag, no-cache, exact bytes/hash, redirect
rejection and missing-asset 404 requirements are unchanged. A 403 remains a failure,
with no retry or alternate identity.

### After the request-identity fix

At 2026-10-16 07:49:56 UTC (16:49:56 JST), a **new** canonical release GET again
returned clean v1.20.1 / `a4de2a226dfa205b8be42fb62a710a1b06271ed8`, with the
same release digest recorded above. The real updated `verify_serving()` then
requested root with the permitted operations identity: HTTP 200, HTML, no-cache,
**ETag absent**, Ray ID `a4b57847ddf0d3ed-KIX`.
It stopped with `Canonical serving/header verification failed`.

| Check | Actual verifier | Separate GET diagnostic after the stop |
| --- | --- | --- |
| Root | HTTP 200; failed ETag requirement; bytes/hash checks not reached | HTTP/MIME/no-cache pass, ETag fails; 1022 bytes and hash fail the inventory comparison |
| `/local/manifest.json` | Not executed | HTTP 200, JSON MIME, ETag, no-cache, 510 bytes and SHA-256 all pass |
| `/local/a4de2a226dfa205b8be42fb62a710a1b06271ed8.missing.whl` | Not executed | HTTP 404 as required |

Root inventory expects **655 bytes**, SHA-256
`5f064aed2bdce84445cf9e15dc6d4cb2dee923ea8e750f9f762b7e051ff5c9d1`;
actual root remains **1022 bytes**, SHA-256
`44e6e333e078407e1ee5f582d6fb0905fb2fda0fddfc41e0343001449135353f`.
The response contains a Cloudflare Insights beacon script. Removing only that
script in an offline diagnostic did **not** restore the expected inventory, so the
full transformation and reason for missing ETag remain unconfirmed. No normalized
content was used for acceptance, and no provider setting was changed.
Manifest SHA-256 is
`471db61d9e3d1f6b4e61a0620c94c07b931712dd3126e23e0c54a964c6f40c58`.

The new catalog GET reports generated_at `2026-10-15T07:35:46.791957Z`, expires_at
`2026-10-15T13:35:46.791957Z`: expired at the observation time. This task did not
generate or publish weather. A fresh candidate build and feed remain unexecuted.

**#266 is not fully resolved for production acceptance.** The signature mismatch
is fixed under explicit owner authorization; canonical HTML ETag and inventory
mismatch still block the unchanged serving gate. Investigate the existing
edge/HTML transformation and published inventory with the owner before any
separately authorized settings or publication change. Do not weaken the gate or
present the separate manifest/404 GETs as a completed `verify_serving()` call.

GitHub GETs during this investigation independently confirm
`STATIC_APPROVED_SHA=a4de2a226dfa205b8be42fb62a710a1b06271ed8`,
`STATIC_AUTOMATION_ENABLED=false`, and workflow `static-production` state `active`.
This is not a fresh Pages management/Rules verification or a publication result.

## Validation and dependency

Related local tests: **625 passed** (includes the 20 new #265 cases and expanded
#266 status/header/content/redirect checks); ruff, mypy (77 files), release-source
validation and diff checks pass. The earlier #265-only run was 597 passes and is
not an additional non-overlapping total. These are synthetic/local checks, not a
candidate build or production acceptance.

PR #267's rerun [38868133221](https://github.com/Yuto-24/AutoNavLog/actions/runs/38868133221)
finished with all eight jobs successful. This follow-up depends on that unmerged
PR and uses `codex/fix-pages-git-controls` as its PR base. Parent CI does not prove
this follow-up's CI result.

## Execution boundary

Do not start prepare using #267's standalone SHA. After the follow-up is reviewed,
merge is explicitly approved and reflected in main, pin the final reviewed tooling
SHA and create a new private prepare store. Recheck current production/approved
SHA, automation/workflow state, complete idle inventory, source configuration and
live Rules at that time. Candidate remains
`e0ad634a8fab2ed48310f48b08c4ec3961cad14b` with the existing
`--rules-transition-v1227` option. Unresolved serving conditions must remain
explicit publication blockers; neither a passing unit test nor a successful
prepare authorizes publish or replaces production acceptance.

References:

- [Issue #265](https://github.com/Yuto-24/AutoNavLog/issues/265)
- [Issue #266](https://github.com/Yuto-24/AutoNavLog/issues/266)
- [Cloudflare error 1010](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)
- [Browser Integrity Check](https://developers.cloudflare.com/waf/tools/browser-integrity-check/)
- [Challenge response headers](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)
- [HTTP User-Agent semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.1.5)
