import { expect, test } from "@playwright/test";
import { fetchDestinationTaf } from "../src/destinationTaf";

const endpoint = "https://taf.example/taf";
test("TAF adapter sends one strict request without credentials", async () => {
  const calls: unknown[] = [];
  const result = await fetchDestinationTaf(endpoint, "RJFM", (async (url, init) => {
    calls.push([String(url), init]);
    return Response.json([{ icaoId: "RJFM" }]);
  }) as typeof fetch);
  expect(result).toEqual({ records: [{ icaoId: "RJFM" }], reason_code: null });
  expect(calls).toEqual([[`${endpoint}?icao=RJFM`, expect.objectContaining({ credentials: "omit", redirect: "error" })]]);
});
test("missing/unsafe configuration never requests Legacy or upstream", async () => {
  const fetcher = (async () => { throw new Error("must not fetch"); }) as typeof fetch;
  for (const url of [undefined, "", "/api/taf", "http://external/taf", "https://u:p@external/taf", `${endpoint}?url=x`, "https://taf.example/other"]) {
    expect((await fetchDestinationTaf(url, "RJFM", fetcher)).reason_code).toBe("TAF_PROXY_NOT_CONFIGURED");
  }
});
test("HTTP/quota/CORS/malformed/oversize errors are TAF unavailable", async () => {
  const cases = [
    () => new Response("quota", { status: 429 }), () => new Response("1027", { status: 503 }),
    () => new Response("<html>"), () => Response.json({ availability: "UNAVAILABLE" }),
    () => Response.json([{ icaoId: "RJFK" }]), () => Response.json([null]),
    () => new Response("bad", { headers: { "Content-Type": "application/json" } }),
    () => new Response(" ".repeat(65537), { headers: { "Content-Type": "application/json" } }),
    () => { throw new TypeError("Failed to fetch"); },
  ];
  for (const fn of cases) expect(await fetchDestinationTaf(endpoint, "RJFM", fn as typeof fetch)).toEqual({ records: [], reason_code: "TAF_FETCH_FAILED" });
});
test("timeout includes stalled body; subsequent request can recover", async () => {
  for (const body of [false, true]) {
    const fetcher = (async (_url, { signal }: RequestInit) => {
      if (body) return new Response(new ReadableStream({ start(c) {
        signal!.addEventListener("abort", () => c.error(new Error("aborted")));
      } }), { headers: { "Content-Type": "application/json" } });
      return new Promise((_, reject) => signal!.addEventListener("abort", () => reject(new Error("aborted"))));
    }) as typeof fetch;
    expect((await fetchDestinationTaf(endpoint, "RJFM", fetcher, 10)).reason_code).toBe("TAF_FETCH_TIMEOUT");
  }
  expect((await fetchDestinationTaf(endpoint, "RJFM", (async () => Response.json([])) as typeof fetch)).reason_code).toBeNull();
});
