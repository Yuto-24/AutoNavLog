import type { ApiErrorPayload, WebState } from "./types";

const SESSION_KEY = "autonavlog.web.session.v1";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly candidates: string[];

  constructor(message: string, code: string, status: number, candidates: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.candidates = candidates;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: ApiErrorPayload = {};
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    // The status text is the only safe fallback for a non-JSON failure.
  }
  return new ApiError(
    payload.error?.message ?? response.statusText ?? "処理に失敗しました。",
    payload.error?.code ?? "REQUEST_FAILED",
    response.status,
    payload.error?.candidates ?? [],
  );
}

export class ApiClient {
  private token: string | null = sessionStorage.getItem(SESSION_KEY);

  async bootstrap(): Promise<WebState> {
    if (this.token) {
      try {
        return await this.request<WebState>("/api/state");
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) {
          throw error;
        }
        this.token = null;
        sessionStorage.removeItem(SESSION_KEY);
      }
    }
    const response = await fetch("/api/session", { method: "POST" });
    if (!response.ok) {
      throw await parseError(response);
    }
    const payload = (await response.json()) as {
      sessionToken: string;
      state: WebState;
    };
    this.token = payload.sessionToken;
    sessionStorage.setItem(SESSION_KEY, payload.sessionToken);
    return payload.state;
  }

  async request<T>(
    path: string,
    options: { method?: string; body?: unknown } = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.token) {
      headers["X-AutoNavLog-Session"] = this.token;
    }
    const response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      throw await parseError(response);
    }
    return (await response.json()) as T;
  }

  resetSession(): void {
    this.token = null;
    sessionStorage.removeItem(SESSION_KEY);
  }

  async downloadTransferAid(): Promise<void> {
    const headers: Record<string, string> = {};
    if (this.token) {
      headers["X-AutoNavLog-Session"] = this.token;
    }
    const response = await fetch("/api/transfer-aid", { headers });
    if (!response.ok) {
      throw await parseError(response);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = match?.[1] ?? "AutoNavLog_transfer_aid.html";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }
}

export async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}
