import { getApp } from "firebase/app";
import { getAuth, type User } from "firebase/auth";
import { collection, doc, getDocFromServer, getDocsFromServer, getFirestore, limit, query, runTransaction, writeBatch } from "firebase/firestore";
import { googleAccount } from "./firebaseAuthProvider";
import { SESSION_KEY } from "./applicationSession";
import { LOCAL_DATABASE } from "./localProjectRepository";

type AccountRecord = { schema: 1; accountId: string; state: "ACTIVE" | "DELETING" | "DELETED" };
const subjectOf = (user: User) => {
  const identities = user.providerData.filter(identity => identity.providerId === "google.com");
  if (identities.length !== 1 || user.providerData.length !== 1 || !identities[0]?.uid) throw new Error("Google アカウントで再認証してください。");
  return identities[0].uid;
};
const recordRef = (user: User) => doc(getFirestore(getApp()), "googleAccounts", subjectOf(user));
const localKey = (user: User) => `autonavlog.account.current.${subjectOf(user)}`;
const retired = (value: string) => `retired:${value}`;
const cachedId = (value: string | null) => value?.startsWith("retired:") ? value.slice(8) : value;
const onlineRequired = () => new Error("アカウント削除にはオンライン接続と再認証が必要です。");

export async function currentAccount(user: User, register = false, closeOldContext: () => void = () => {}): Promise<{ accountId: string; deleting: boolean }> {
  const legacyId = (await googleAccount(user)).account_id;
  const key = localKey(user);
  let serverConfirmedInactive = false;
  if (!navigator.onLine) {
    const cached = localStorage.getItem(key);
    if (cached && !cached.startsWith("retired:")) return { accountId: cached, deleting: false };
    throw new Error("オンライン接続と再認証が必要です。");
  }
  try {
    const ref = recordRef(user);
    const snapshot = await getDocFromServer(ref);
    let value = snapshot.data() as AccountRecord | undefined;
    if (!value) {
      value = { schema: 1, accountId: legacyId, state: "ACTIVE" };
      await runTransaction(ref.firestore, async transaction => {
        const existing = await transaction.get(ref);
        if (!existing.exists()) transaction.set(ref, value!);
        else value = existing.data() as AccountRecord;
      });
    }
    if (value.schema !== 1 || typeof value.accountId !== "string") throw new Error("アカウント情報を確認できません。");
    serverConfirmedInactive = value.state !== "ACTIVE";
    const previous = cachedId(localStorage.getItem(key));
    if (value.state !== "ACTIVE" || (previous && previous !== value.accountId)) closeOldContext();
    if (value.state !== "ACTIVE" || (previous && previous !== value.accountId))
      localStorage.setItem(key, retired(previous ?? value.accountId));
    if (value.state === "DELETING") {
      try {
        await removeLocalAccount(value.accountId);
        if (previous && previous !== value.accountId) await removeLocalAccount(previous);
      } catch (error) {
        throw new Error("ACCOUNT_DELETION_PENDING", { cause: error });
      }
      return { accountId: value.accountId, deleting: true };
    }
    if (value.state === "DELETED") {
      await removeLocalAccount(value.accountId);
      if (previous && previous !== value.accountId) await removeLocalAccount(previous);
      if (!register) {
        localStorage.removeItem(key);
        throw new Error("ACCOUNT_DELETED");
      }
      const next = { schema: 1, accountId: `account_v2_${crypto.randomUUID()}`, state: "ACTIVE" } as const;
      value = await runTransaction(ref.firestore, async transaction => {
        const existing = (await transaction.get(ref)).data() as AccountRecord;
        if (existing.state !== "DELETED") return existing;
        transaction.set(ref, next);
        return next;
      });
    }
    if (previous && previous !== value.accountId) await removeLocalAccount(previous);
    if (value.accountId !== legacyId) await removeLocalAccount(legacyId);
    localStorage.setItem(key, value.accountId);
    return { accountId: value.accountId, deleting: value.state === "DELETING" };
  } catch (error) {
    // Cached identity permits existing Local work during an outage. A missing
    // cache on a fresh device must never guess which generation owns its data.
    if (!serverConfirmedInactive && (!navigator.onLine || ["unavailable", "deadline-exceeded"].includes((error as { code?: string })?.code ?? ""))) {
      const cached = localStorage.getItem(key);
      if (cached && !cached.startsWith("retired:")) return { accountId: cached, deleting: false };
    }
    throw error;
  }
}

export async function removeLocalAccount(accountId: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase(`autonavlog.projects.${accountId}`);
    const blocked = () => new Error("端末内のデータを削除できませんでした。別のタブを閉じて、削除を再試行してください。");
    // Some browsers leave a blocked delete pending without delivering onblocked.
    // Keep the server in DELETING and make the operation retryable.
    const timeout = setTimeout(() => reject(blocked()), 10_000);
    request.onsuccess = () => { clearTimeout(timeout); resolve(); };
    request.onerror = () => { clearTimeout(timeout); reject(request.error); };
    request.onblocked = () => { clearTimeout(timeout); reject(blocked()); };
  });
  sessionStorage.removeItem(`${SESSION_KEY}.${accountId}`);
  // #185 keeps claimed anonymous originals for interrupted import recovery.
  // Once their owner is deleted, they must not remain as hidden local copies.
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.open(LOCAL_DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore("projects", { keyPath: "id" });
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const tx = db.transaction("projects", "readwrite");
      const cursor = tx.objectStore("projects").openCursor();
      cursor.onsuccess = () => {
        const row = cursor.result;
        if (!row) return;
        if (row.value?.claimedAccount === accountId) row.delete();
        row.continue();
      };
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onabort = () => { db.close(); reject(tx.error); };
    };
  });
}

export async function deleteCurrentAccount(user: User, expectedId: string, onDeleting: () => void): Promise<void> {
  if (!navigator.onLine) throw onlineRequired();
  try { await user.getIdToken(true); } catch { throw onlineRequired(); }
  if (getAuth(getApp()).currentUser !== user) throw onlineRequired();
  const ref = recordRef(user);
  const snapshot = await getDocFromServer(ref).catch(() => { throw onlineRequired(); });
  const value = snapshot.data() as AccountRecord | undefined;
  if (!value || value.accountId !== expectedId || value.state === "DELETED") throw new Error("現在のアカウントを確認できません。再ログインしてください。");
  if (value.state === "ACTIVE") await runTransaction(ref.firestore, async transaction => {
    const current = (await transaction.get(ref)).data() as AccountRecord;
    if (current.accountId !== expectedId || current.state !== "ACTIVE") throw new Error("アカウントの状態が変わりました。再試行してください。");
    transaction.update(ref, { state: "DELETING" });
  });
  // The server gate is now durable. An interrupted deletion must not reopen
  // this generation from the deleting device's offline cache.
  localStorage.setItem(localKey(user), retired(expectedId));
  onDeleting();
  const db = getFirestore(getApp());
  const subject = subjectOf(user);
  const projects = expectedId.startsWith("account_v2_")
    ? collection(db, "googleAccounts", subject, "generations", expectedId, "projects")
    : collection(db, "googleAccounts", subject, "projects");
  // The durable DELETING gate denies all reads/writes from old clients. Repeat
  // physical cleanup from the server on retry after any partial batch failure.
  while (true) {
    const rows = await getDocsFromServer(query(projects, limit(10)));
    if (rows.empty) break;
    const batch = writeBatch(db);
    for (const row of rows.docs) batch.delete(row.ref);
    await batch.commit();
  }
  await removeLocalAccount(expectedId);
  await runTransaction(ref.firestore, async transaction => {
    const current = (await transaction.get(ref)).data() as AccountRecord;
    if (current.accountId !== expectedId || current.state !== "DELETING") throw new Error("アカウントの状態が変わりました。再試行してください。");
    transaction.update(ref, { state: "DELETED" });
  });
  localStorage.removeItem(localKey(user));
}
