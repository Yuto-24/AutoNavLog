import type { Page } from "@playwright/test";

// Exercise a promise-origin React commit arriving after animation-frame callbacks.
// The real Local Worker and repository still run; only the renderer's host task
// is deferred through a native frame and MessageChannel, without a timed sleep.
export async function installPlanningRenderDelay(page: Page) {
  await page.addInitScript(() => {
    const state = { active: false, recognized: false, delayed: 0, focusFrameBeforeCommit: null as boolean | null };
    (window as any).planningRenderDelay = state;
    const NativeChannel = window.MessageChannel;
    const nativeFrame = requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => {
      // Identify Planning's focus callback by its DOM operations. Unrelated
      // callbacks and the scheduler-delay frames retain the native path.
      const body = String(callback);
      if (!state.active || !["scrollIntoView", "querySelector", "focus"].every(operation => body.includes(operation))) {
        return nativeFrame(callback);
      }
      return nativeFrame(timestamp => {
        state.focusFrameBeforeCommit = !document.querySelector('[aria-label="経路と飛行計画"]');
        try { callback(timestamp); }
        finally {
          // A scheduler task can precede the async route's focus request.
          // Resume only after the requested focus frame has been observed.
          for (const id of pending.keys()) resume.port2.postMessage(id);
        }
      });
    };
    const pending = new Map<number, () => void>();
    let next = 0;
    const resume = new NativeChannel();
    resume.port1.onmessage = event => {
      if (state.active && state.focusFrameBeforeCommit === null) return;
      const work = pending.get(event.data);
      pending.delete(event.data);
      work?.();
    };
    let channels = 0;
    window.MessageChannel = class extends NativeChannel {
      constructor() {
        super();
        // The pinned React 19 scheduler creates the first main-thread channel.
        // Check its handler so a future scheduler change cannot silently pass.
        if (++channels !== 1) return;
        const port = this.port1;
        const property = Object.getOwnPropertyDescriptor(MessagePort.prototype, "onmessage")!;
        let handler: ((event: MessageEvent) => void) | null = null;
        Object.defineProperty(port, "onmessage", {
          configurable: true,
          get: () => handler,
          set(value) {
            handler = value;
            state.recognized = String(value).includes("unstable_now");
            property.set!.call(port, (event: MessageEvent) => {
              const work = () => value.call(port, event);
              if (!state.active) { work(); return; }
              state.delayed += 1;
              const id = ++next;
              pending.set(id, work);
              nativeFrame(() => resume.port2.postMessage(id));
            });
          },
        });
      }
    };
    document.addEventListener("DOMContentLoaded", () => {
      new MutationObserver(() => {
        if (document.querySelector('[aria-label="経路と飛行計画"]')) state.active = false;
      }).observe(document.documentElement, { childList: true, subtree: true });
    });
  });
}

export async function deferPlanningRender(page: Page) {
  await page.evaluate(() => {
    const state = (window as any).planningRenderDelay;
    if (!state?.recognized) throw new Error("React scheduler host channel was not identified");
    state.focusFrameBeforeCommit = null;
    state.active = true;
  });
}
