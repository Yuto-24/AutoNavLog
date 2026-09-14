import type { Page } from "@playwright/test";

// Existing browser assertions read the real backend state. Observe the adapter's
// private handle without exposing a production debug API or relying on its Cookie.
export async function observeLegacySession(page: Page) {
  await page.addInitScript(() => {
    const original = window.fetch;
    let token: string | undefined;
    let initialized!: () => void;
    const ready = new Promise<void>(resolve => { initialized = resolve; });
    window.fetch = async (input, init) => {
      const path = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url;
      if (path.startsWith("/api/") && path !== "/api/application-session") {
        await ready;
        const headers = new Headers(init?.headers);
        if (!headers.has("X-AutoNavLog-Session")) headers.set("X-AutoNavLog-Session", token!);
        init = { ...init, headers };
      }
      const response = await original(input, init);
      if (path === "/api/application-session" && init?.method === "POST" && response.ok) {
        token = (await response.clone().json()).token;
        initialized();
      }
      return response;
    };
  });
}
