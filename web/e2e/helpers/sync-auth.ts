import type { BrowserContext } from "@playwright/test";
function jwt(subject: string) {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  return `${part({ alg: "none", typ: "JWT" })}.${part({ aud: "demo-autonavlog-sync", iss: "https://securetoken.google.com/demo-autonavlog-sync", sub: `firebase-${subject}`, iat: now, exp: now + 3600, auth_time: now, firebase: { sign_in_provider: "google.com", identities: { "google.com": [subject] } } })}.`;
}
export async function backend(context: BrowserContext) {
  const state = { failure: "", lookupCount: 0 };
  await context.route("**/api/**", route => route.abort());
  await context.route(/https:\/\/(identitytoolkit|securetoken|www)\.googleapis\.com\//, async route => {
    if (state.failure === "offline") { await route.abort("internetdisconnected"); return; }
    if (state.failure) {
      await route.fulfill({ status: state.failure === "INTERNAL_ERROR" ? 503 : 400, json: { error: { message: state.failure } } });
      return;
    }
    const request = route.request();
    const body = request.postDataJSON();
    if (request.url().includes("accounts:signInWithIdp")) {
      const subject = new URLSearchParams(body.postBody).get("id_token")!;
      await route.fulfill({ json: { localId: `firebase-${subject}`, displayName: subject, email: `${subject}@example.test`,
        providerId: "google.com", federatedId: subject, idToken: jwt(subject), refreshToken: `refresh-${subject}`, expiresIn: "3600", rawUserInfo: JSON.stringify({ sub: subject }) } });
    } else if (request.url().includes("accounts:lookup")) {
      state.lookupCount++;
      const claims = JSON.parse(Buffer.from(body.idToken.split(".")[1], "base64url").toString());
      const subject = claims.sub.replace("firebase-", "");
      await route.fulfill({ json: { users: [{ localId: claims.sub, displayName: subject, email: `${subject}@example.test`, emailVerified: true,
        providerUserInfo: [{ providerId: "google.com", rawId: subject, displayName: subject, email: `${subject}@example.test` }] }] } });
    } else if (request.url().includes("/token")) {
      const subject = new URLSearchParams(request.postData()!).get("refresh_token")!.replace("refresh-", "");
      await route.fulfill({ json: { user_id: `firebase-${subject}`, id_token: jwt(subject), access_token: jwt(subject), refresh_token: `refresh-${subject}`, expires_in: "3600" } });
    } else await route.fulfill({ json: { authorizedDomains: ["127.0.0.1", "localhost"] } });
  });
  return state;
}
