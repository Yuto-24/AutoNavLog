# NavMate Account deletion (#181)

The Account dialog starts deletion only after an explicit confirmation. The
browser requires a current Google identity, a fresh Firebase token, and a
server read of the corresponding account record. Offline or expired credentials
do not queue a deletion. Project deletion and its offline Undo remain the
separate #185 operation.

The `googleAccounts/{subject}` record stores an internal account ID and one of
`ACTIVE`, `DELETING`, or `DELETED`. The first ID preserves the #184 deterministic
`account_v1_…` mapping and the existing #185/#186 Project path. Deletion first
commits `DELETING`; Firestore Rules then reject Project writes from every device.
The deleting device removes the account's remote Project documents in small
idempotent batches, removes its account IndexedDB namespace and anonymous
originals claimed by that ID, then marks the account `DELETED`. A failed cleanup
leaves `DELETING` in place and offers a retry after reconnect/re-authentication;
the old generation never reopens for normal syncing.

On another device, the next successful online account check closes its old
Application context, marks the cached generation unusable offline, and removes
that account's Local copies. Until connectivity
returns, the already cached Local copy can remain visible on that device, but
Rules prevent it from uploading. A deleted identity does not automatically
create a new NavMate account on token refresh. An explicit Google login may
register a new `account_v2_…` ID. Its Project documents have a separate
generation path and cannot import Local rows claimed by an older account.
Other Google identities and their namespaces are unaffected. The Google Account
and Firebase Authentication user are not deleted.

The #186 Legacy link is tied to the original account ID. A newly registered
generation skips that migration so it cannot inherit deleted Legacy data. The
link dialog itself remains an authentication-only operation. #182 Share is not
implemented yet; a future Share adapter must gate access on the issuer's active
account generation and drain all snapshots before marking it `DELETED`.

The account record and Rules must be deployed together before exposing this UI
in a Local build. The first online login creates the account record; a returning
device with a cached ID may continue Local work during a transient outage.
Deleting account data is irreversible. Physical browser/production acceptance
must use a real Google account, deployed Rules, and two devices, and separately
verify the old device after reconnect plus a deliberate re-registration.
