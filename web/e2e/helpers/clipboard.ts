import type { Page } from "@playwright/test";

export async function disableClipboardRead(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText: async () => { throw new DOMException("Denied", "NotAllowedError"); } },
    });
  });
}
