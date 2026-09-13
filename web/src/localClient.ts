import { ApplicationError } from "./application";
import type { ApplicationErrorDetails } from "./application";
import { wrap } from "comlink";
import type { LocalWorker } from "./local.worker";

export class LocalClient {
  private worker = new Worker(new URL("./local.worker.ts", import.meta.url), { type: "module" });
  private remote = wrap<LocalWorker>(this.worker);
  private failed: Promise<never>;
  private terminalError: Error | undefined;
  private fail!: (error: Error) => void;

  constructor() {
    this.failed = new Promise((_, reject) => {
      this.fail = (error) => {
        this.terminalError = error;
        this.worker.terminate();
        reject(error);
      };
      this.worker.addEventListener("error", (event) => this.fail(new Error(event.message || "Local Worker failed")));
      this.worker.addEventListener("messageerror", () => this.fail(new Error("Local Worker message failed")));
    });
    void this.failed.catch(() => undefined);
  }

  async request<T>(path: string, body?: unknown): Promise<T> {
    if (this.terminalError) throw this.terminalError;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const json = await Promise.race([
        this.remote.request(path, body),
        this.failed,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            const error = new Error("Local処理がタイムアウトしました。画面を再読み込みしてください。");
            this.fail(error);
            reject(error);
          }, 10 * 60_000);
        }),
      ]);
      const payload = JSON.parse(json) as T | {
        error: { code: string; message: string; details?: ApplicationErrorDetails };
      };
      if (payload && typeof payload === "object" && "error" in payload) {
        const error = payload.error;
        throw new ApplicationError(error.message, error.code, error.details);
      }
      return payload as T;
    } finally {
      clearTimeout(timer);
    }
  }

  dispose() {
    this.fail(new Error("Local session was reset."));
  }
}
