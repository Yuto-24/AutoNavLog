# Root HTML Analytics verification (#266)

## Scope and dependency

This incremental change starts at PR #268 HEAD
`0a7add84c2616c80846ff9aea40723b48040ec3e`, which depends on unmerged Draft
PR #267 (`1f04cd3adc406df6d21ba8cf1eeb56d03af7f969`). Both were checked OPEN/Draft
before work; their existing checkouts were clean and left untouched. Root HTML
verification alone changes. No settings, billing, credentials/permissions, Rules,
merge, prepare/publish, schedules or approved SHA are changed.

## Evidence and fixed permission

The prior `/tmp/autonavlog-issue266-readonly-20261017/REPORT.md` and raw payload
establish the observed insertion, including exact src, integrity, attribute order,
beacon JSON, spaces and newline. The owner confirmed these dashboard settings in
this implementation chat on 2026-10-17:

- Pages **navmate** Web Analytics: disabled.
- Account Web Analytics **yuto24.com**: automatic setup.
- RUM: enabled, excluding EU visitor data; displayed setting **enable_lite**.
- Analytics displays measurements for **navmate.yuto24.com**.

This is owner-reported dashboard evidence; the agent did not access management APIs
or infer a causal mapping between `enable_lite` and individual beacon fields.
The root response itself does not establish the source of ETag removal.

The only admitted insertion is the complete byte literal `ROOT_ANALYTICS_INSERTION`:
366 script bytes plus exactly one LF, SHA-256
`b6be984c8109d0590449b61544a081955ec1c3e10f453a97cb17afa54924969f`.
It includes src suffix `v4bc70e2c01a94c73b74392e4234840661791215815920`, the complete
sha512 integrity attribute, `type="module"`, `crossorigin="anonymous"`, and exact
`data-cf-beacon` JSON (`version=2024.11.0`, public token
`95f71c67cb5a41d3ab040fe18f7bcaa4`, `r=1`, `spa=2`).
The literal must occur once, after the original two spaces, immediately before
the exact final `</body>\n</html>\n` bytes. Only that literal is removed for comparison;
all original bytes, including those spaces, remain. Position is relative to the
closing suffix, not a fixed numeric offset into future builds. Its observed offsets
in the saved 1022-byte payload are `[639,1006)`.

Root accepts exact inventory bytes/SHA-256 first, or this one insertion followed by
exact inventory bytes/SHA-256. It retains HTTP 200, text/html, existing no-cache
semantics, the 8 MiB bound, redirect refusal and the honest operations request
identity. Root ETag presence/value is recorded, not required. Manifest ETag/hash/size,
missing-asset 404, Rules, MSM freshness and all inventory gates remain unchanged.

The verifier stores untouched root payload bytes as `serving-root.body` and writes
`serving-root.json` next to the candidate upload directory, including raw and compared
bytes/SHA-256, expected inventory, ETag, result, match mode and admitted insertion hash.
Rejected bodies retain failure evidence. Oversized bodies retain only the bounded
8 MiB + 1 prefix, explicitly marked `raw_complete=false`; they always fail.
Redirects/transport failures can occur before a body is available. These files record
the root check only; `result=OK` does not assert completion of later serving checks.

**Cloudflare changes require renewed evidence and review.** Do not auto-learn a
new script, parse and strip arbitrary HTML, normalize whitespace, remove a fixed
number of bytes, or execute analytics JavaScript to validate it.

## Current production GET: 2026-10-17 09:58 JST

Raw requests/results are retained at `/tmp/autonavlog-issue266-validation/live/`.
`live_get.py` in its parent captures transport observations while calling the real
`Operator.verify_serving()` and, separately, `operations.monitor()`. Only authorized
GETs to the canonical origin are used, with no retry, redirect, query, new identity
or authentication. No validation function is replaced.

| Check | Observed result |
| --- | --- |
| Fresh release input | HTTP 200; 28481 bytes; `54147dd5af57ce47a903e478d449d7c91ad1e15668ad1bc759e726effc07e946`; ETag present; no-cache; application/json |
| Release identity | clean v1.20.1 / `a4de2a226dfa205b8be42fb62a710a1b06271ed8` |
| Actual root status/headers/budget | HTTP 200; text/html; no-cache; 1022 bytes; ETag absent (recorded); within budget |
| Actual root content check | **BLOCKED**: script src/integrity changed; no permitted insertion at the pinned position |
| Actual manifest check | **NOT RUN**, stopped at root |
| Actual missing asset check | **NOT RUN**, stopped at root |
| Separate real MSM monitor | **BLOCKED**, catalog expired at 2026-10-15 22:35:46.791957 JST |
| Full `Operator.verify()` / candidate inventory gate / Pages identity / Rules | **NOT RUN**; no publication or production acceptance claim |

The new raw root SHA-256 is
`563d09a93aee94ebf8d68b0cc69eaf205f2b97e32c10c7ef3c884fdacf763340`.
CF-Ray `a4bb5aa35dacd429-KIX`. The new script src suffix is
`vce701823e78c4301a35b7e07294d87301792088814092`, integrity
`sha512-UKNk8GbxkABxKcXYuLkO16EleLGUkcvJWtpSNmExE0Too3ZaFAxTscNWEc1OdAv5/BikHawapSCaiD2XCmp+Bw==`.
The remaining attributes, beacon configuration, position and newline are unchanged.
The new script + LF is still 367 bytes, SHA-256
`139e1299438bceecb0a33814f2e7947ec22febf072f1d0f533087a1ee91bd461`.
Offline removal of exactly that observed span yields 655 bytes / SHA-256
`5f064aed2bdce84445cf9e15dc6d4cb2dee923ea8e750f9f762b7e051ff5c9d1`, matching inventory.
**This is diagnostic evidence, not an allowed transformation or a passing verification.**
The owner instructed keeping the original fixed value, continuing its rejection
regression/Draft PR, and retaining the new bytes separately without adding them.
Both raw captures are retained as provenance-documented test fixtures.

Current published original HTML remains unavailable. The old 2026-09-24 archive
has a different JS reference/hash. Neither inventory equality nor removal of a
captured insertion is direct comparison with the current publication-time original.
No reconstructed body is labelled or stored as that original.

## Validation and follow-up

Tests call real `verify_serving()` through all three requests for exact HTML with
and without ETag and the fixed insertion. They reject duplicate/foreign/modified
scripts, src/integrity/beacon/attribute/position/LF drift, JS/CSS/other HTML drift,
HTTP failures, redirects, MIME/cache violations and oversize. Manifest mismatch
or missing ETag and missing-asset 200/403 still stop the real sequence after a
successful root. The newly observed provider script is a separate rejection fixture.

Keep #266 open. Current root drift and expired MSM remain production blockers.
After renewed approval of any insertion change, revalidate with an authorized fresh
GET. Before publication, obtain explicit merge approval for all required PRs, reflect
them in main, pin the final tooling SHA, and then update the publication procedure.
Do not prepare from #267, #268 or this unmerged branch alone.
